// This is an open source non-commercial project. Dear PVS-Studio, please check it.
// PVS-Studio Static Code Analyzer for C, C++ and C#: http://www.viva64.com
// ******************************************************************
// *
// *  This file is part of the Cxbx project.
// *
// *  Cxbx and Cxbe are free software; you can redistribute them
// *  and/or modify them under the terms of the GNU General Public
// *  License as published by the Free Software Foundation; either
// *  version 2 of the license, or (at your option) any later version.
// *
// *  This program is distributed in the hope that it will be useful,
// *  but WITHOUT ANY WARRANTY; without even the implied warranty of
// *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// *  GNU General Public License for more details.
// *
// *  You should have recieved a copy of the GNU General Public License
// *  along with this program; see the file COPYING.
// *  If not, write to the Free Software Foundation, Inc.,
// *  59 Temple Place - Suite 330, Bostom, MA 02111-1307, USA.
// *
// *  (c) 2002-2003 Aaron Robinson <caustik@caustik.com>
// *
// *  All rights reserved
// *
// ******************************************************************

#define LOG_PREFIX CXBXR_MODULE::KRNL


#include <core\kernel\exports\xboxkrnl.h>
#include <core\kernel\exports\EmuKrnlKi.h>
#include "core\kernel\support\EmuFS.h"
#include "core\kernel\support\NativeHandle.h"
#include <cstdio>
#include <cctype>
#include <clocale>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <vector>
#include <algorithm>

#include "Logging.h"
#include "EmuKrnlLogging.h"
#include "EmuKrnl.h" // for HalSystemInterrupts
#include "EmuKrnlKi.h" // for KiLockDispatcherDatabase
#include "core\kernel\init\CxbxKrnl.h"

// prevent name collisions
namespace NtDll
{
    #include "core\kernel\support\EmuNtDll.h"
};

// See also :
// https://github.com/reactos/reactos/blob/40a16a9cf1cdfca399e9154b42d32c30b63480f5/reactos/drivers/filesystems/udfs/Include/env_spec_w32.h
void InitializeListHead(xbox::PLIST_ENTRY pListHead)
{
	pListHead->Flink = pListHead->Blink = pListHead;
}

bool IsListEmpty(xbox::PLIST_ENTRY pListHead)
{
	return (pListHead->Flink == pListHead);
}

void InsertHeadList(xbox::PLIST_ENTRY pListHead, xbox::PLIST_ENTRY pEntry)
{
	xbox::PLIST_ENTRY _EX_ListHead = pListHead;
	xbox::PLIST_ENTRY _EX_Flink = _EX_ListHead->Flink;

	pEntry->Flink = _EX_Flink;
	pEntry->Blink = _EX_ListHead;
	_EX_Flink->Blink = pEntry;
	_EX_ListHead->Flink = pEntry;
}

void InsertTailList(xbox::PLIST_ENTRY pListHead, xbox::PLIST_ENTRY pEntry)
{
	xbox::PLIST_ENTRY _EX_ListHead = pListHead;
	xbox::PLIST_ENTRY _EX_Blink = _EX_ListHead->Blink;

	pEntry->Flink = _EX_ListHead;
	pEntry->Blink = _EX_Blink;
	_EX_Blink->Flink = pEntry;
	_EX_ListHead->Blink = pEntry;
}

//#define RemoveEntryList(e) do { PLIST_ENTRY f = (e)->Flink, b = (e)->Blink; f->Blink = b; b->Flink = f; (e)->Flink = (e)->Blink = NULL; } while (0)

// Returns TRUE if the list has become empty after removing the element, FALSE otherwise.
// NOTE: this function is a mess. _EX_Flink and _EX_Flink should never be nullptr, and it should never be called on a detached element either. Try to fix
// the bugs in the caller instead of trying to handle it here with these hacks
xbox::boolean_xt RemoveEntryList(xbox::PLIST_ENTRY pEntry)
{
	xbox::PLIST_ENTRY _EX_Flink = pEntry->Flink;
	xbox::PLIST_ENTRY _EX_Blink = pEntry->Blink;

	if (_EX_Blink != nullptr) {
		_EX_Blink->Flink = _EX_Flink;
	}

	if (_EX_Flink != nullptr) {
		_EX_Flink->Blink = _EX_Blink;
	}

	if (_EX_Blink != nullptr && _EX_Flink != nullptr) {
		return (_EX_Flink == _EX_Blink);
	}
	// If we reach here then it means we have erroneously been called on a detached element. In this case,
	// always report FALSE to avoid possible side effects
	return FALSE;
}

xbox::PLIST_ENTRY RemoveHeadList(xbox::PLIST_ENTRY pListHead)
{
	xbox::PLIST_ENTRY Result = pListHead->Flink;

	RemoveEntryList(pListHead->Flink);
	return Result;
}

xbox::PLIST_ENTRY RemoveTailList(xbox::PLIST_ENTRY pListHead)
{
	xbox::PLIST_ENTRY Result = pListHead->Blink;

	RemoveEntryList(pListHead->Blink);
	return Result;
}

// Interrupts

extern volatile DWORD HalInterruptRequestRegister;

volatile bool g_bInterruptsEnabled = true;

bool DisableInterrupts()
{
	bool Result = g_bInterruptsEnabled;
	g_bInterruptsEnabled = false;
	return Result;
}

void RestoreInterruptMode(bool value)
{
	g_bInterruptsEnabled = value;
}

void KiUnexpectedInterrupt()
{
	xbox::KeBugCheck(TRAP_CAUSE_UNKNOWN); // see
	CxbxrAbort("Unexpected Software Interrupt!");
}

void CallSoftwareInterrupt(const xbox::KIRQL SoftwareIrql)
{
	switch (SoftwareIrql) {
	case PASSIVE_LEVEL:
		KiUnexpectedInterrupt();
		break;
	case APC_LEVEL: // = 1 HalpApcInterrupt
		xbox::KiExecuteKernelApc();
		break;
	case DISPATCH_LEVEL: // = 2
		// This can be recursively called by KiUnlockDispatcherDatabase and KfLowerIrql, so avoid calling DPCs again if the current one has queued yet another one
		if (!IsDpcActive()) { // Avoid KeIsExecutingDpc(), as that logs
			ExecuteDpcQueue();
		}
		break;
	case APC_LEVEL | DISPATCH_LEVEL: // = 3
		KiUnexpectedInterrupt();
		break;
	default:
		// Software Interrupts > 3 map to Hardware Interrupts [4 = IRQ0]
		// This is used to trigger hardware interrupt routines from software
		if (EmuInterruptList[SoftwareIrql - 4]->Connected) {
			HalSystemInterrupts[SoftwareIrql - 4].Trigger(EmuInterruptList[SoftwareIrql - 4]);
		}
		break;
	}

	HalInterruptRequestRegister ^= (1 << SoftwareIrql);
}

bool AddWaitObject(xbox::PKTHREAD kThread, xbox::PLARGE_INTEGER Timeout)
{
	// Use the built-in ktimer as a dummy wait object, so that KiUnwaitThreadAndLock can still work
	xbox::KiTimerLock();
	xbox::PKWAIT_BLOCK WaitBlock = &kThread->TimerWaitBlock;
	kThread->WaitBlockList = WaitBlock;
	xbox::PKTIMER Timer = &kThread->Timer;
	WaitBlock->NextWaitBlock = WaitBlock;
	Timer->Header.WaitListHead.Flink = &WaitBlock->WaitListEntry;
	Timer->Header.WaitListHead.Blink = &WaitBlock->WaitListEntry;
	if (Timeout && Timeout->QuadPart) {
		// Setup a timer so that KiTimerExpiration can discover the timeout and yield to us.
		// Otherwise, we will only be able to discover the timeout when Windows decides to schedule us again, and testing shows that
		// tends to happen much later than the due time
		if (xbox::KiInsertTreeTimer(Timer, *Timeout) == FALSE) {
			// Sanity check: set WaitBlockList to nullptr so that we can catch the case where a waiter starts a new wait but forgets to setup a new wait block. This
			// way, we will crash instead of silently using the pointer to the old block
			kThread->WaitBlockList = xbox::zeroptr;
			xbox::KiTimerUnlock();
			return false;
		}
	}
	kThread->State = xbox::Waiting;
	xbox::KiTimerUnlock();
	return true;
}

// This masks have been verified to be correct against a kernel dump
const DWORD IrqlMasks[] = {
	0xFFFFFFFE, // IRQL 0
	0xFFFFFFFC, // IRQL 1 (APC_LEVEL)
	0xFFFFFFF8, // IRQL 2 (DISPATCH_LEVEL)
	0xFFFFFFF0, // IRQL 3
	0x03FFFFF0, // IRQL 4
	0x01FFFFF0, // IRQL 5
	0x00FFFFF0, // IRQL 6
	0x007FFFF0, // IRQL 7
	0x003FFFF0, // IRQL 8
	0x001FFFF0, // IRQL 9
	0x000FFFF0, // IRQL 10
	0x0007FFF0, // IRQL 11
	0x0003FFF0, // IRQL 12
	0x0001FFF0, // IRQL 13 (same as IRQL 14)
	0x0001FFF0, // IRQL 14 (same as IRQL 13)
	0x00017FF0, // IRQL 15
	0x00013FF0, // IRQL 16
	0x00011FF0, // IRQL 17 (same as IRQL 18)
	0x00011FF0, // IRQL 18 (same as IRQL 17)
	0x000117F0, // IRQL 19
	0x000113F0, // IRQL 20
	0x000111F0, // IRQL 21
	0x000110F0, // IRQL 22
	0x00011070, // IRQL 23
	0x00011030, // IRQL 24
	0x00011010, // IRQL 25
	0x00010010, // IRQL 26 (PROFILE_LEVEL)
	0x00000010, // IRQL 27
	0x00000000, // IRQL 28 (SYNC_LEVEL)
	0x00000000, // IRQL 29
	0x00000000, // IRQL 30
	0x00000000, // IRQL 31 (HIGH_LEVEL)
};


// ******************************************************************
// * 0x0033 - InterlockedCompareExchange()
// ******************************************************************
// Source:ReactOS
XBSYSAPI EXPORTNUM(51) xbox::long_xt FASTCALL xbox::KRNL(InterlockedCompareExchange)
(
	IN OUT PLONG VOLATILE Destination,
	IN long_xt  Exchange,
	IN long_xt  Comparand
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(Destination)
		LOG_FUNC_ARG(Exchange)
		LOG_FUNC_ARG(Comparand)
		LOG_FUNC_END;

	LONG res = InterlockedCompareExchange((NtDll::PLONG)Destination, (NtDll::LONG)Exchange, (NtDll::LONG)Comparand);

	RETURN(res);
}

// ******************************************************************
// * 0x0034 - InterlockedDecrement()
// ******************************************************************
// Source:ReactOS
XBSYSAPI EXPORTNUM(52) xbox::long_xt FASTCALL xbox::KRNL(InterlockedDecrement)
(
	IN OUT PLONG Addend
)
{
	LOG_FUNC_ONE_ARG(Addend);

	LONG res = InterlockedDecrement((NtDll::PLONG)Addend);

	RETURN(res);
}

// ******************************************************************
// * 0x0035 - InterlockedIncrement()
// ******************************************************************
// Source:ReactOS
XBSYSAPI EXPORTNUM(53) xbox::long_xt FASTCALL xbox::KRNL(InterlockedIncrement)
(
	IN OUT PLONG Addend
)
{
	LOG_FUNC_ONE_ARG(Addend);

	LONG res = InterlockedIncrement((NtDll::PLONG)Addend);

	RETURN(res);
}

// ******************************************************************
// * 0x0036 - InterlockedExchange()
// ******************************************************************
// Source:ReactOS
XBSYSAPI EXPORTNUM(54) xbox::long_xt FASTCALL xbox::KRNL(InterlockedExchange)
(
	IN PLONG VOLATILE Destination,
	IN long_xt Value
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(Destination)
		LOG_FUNC_ARG(Value)
		LOG_FUNC_END;

	LONG res = InterlockedExchange((NtDll::PLONG)Destination, (NtDll::LONG)Value);

	RETURN(res);
}

// ******************************************************************
// * 0x0037 - InterlockedExchangeAdd()
// ******************************************************************
// Source:ReactOS
XBSYSAPI EXPORTNUM(55) xbox::long_xt FASTCALL xbox::KRNL(InterlockedExchangeAdd)
(
	IN PLONG VOLATILE Addend,
	IN long_xt	Value
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(Addend)
		LOG_FUNC_ARG(Value)
		LOG_FUNC_END;

	LONG res = InterlockedExchangeAdd((NtDll::PLONG)Addend, (NtDll::LONG)Value);

	RETURN(res);
}

// ******************************************************************
// * 0x0038 - InterlockedFlushSList()
// ******************************************************************
// Source:ReactOS
// Dxbx Note : The Xbox1 SINGLE_LIST strucures are the same as in WinNT
XBSYSAPI EXPORTNUM(56) xbox::PSINGLE_LIST_ENTRY FASTCALL xbox::KRNL(InterlockedFlushSList)
(
	IN xbox::PSLIST_HEADER ListHead
)
{
	LOG_FUNC_ONE_ARG(ListHead);

	PSINGLE_LIST_ENTRY res = (PSINGLE_LIST_ENTRY)InterlockedFlushSList((::PSLIST_HEADER)ListHead);

	RETURN(res);
}

// ******************************************************************
// * 0x0039 - InterlockedPopEntrySList()
// ******************************************************************
// Source:ReactOS
XBSYSAPI EXPORTNUM(57) xbox::PSLIST_ENTRY FASTCALL xbox::KRNL(InterlockedPopEntrySList)
(
	IN PSLIST_HEADER ListHead
)
{
	LOG_FUNC_ONE_ARG(ListHead);

	PSLIST_ENTRY res = (PSLIST_ENTRY)InterlockedPopEntrySList((::PSLIST_HEADER)ListHead);

	RETURN(res);
}

// ******************************************************************
// * 0x003A - InterlockedPushEntrySList()
// ******************************************************************
// Source:ReactOS
XBSYSAPI EXPORTNUM(58) xbox::PSLIST_ENTRY FASTCALL xbox::KRNL(InterlockedPushEntrySList)
(
	IN PSLIST_HEADER ListHead,
	IN PSLIST_ENTRY ListEntry
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(ListHead)
		LOG_FUNC_ARG(ListEntry)
		LOG_FUNC_END;

	PSLIST_ENTRY res = (PSLIST_ENTRY)InterlockedPushEntrySList((::PSLIST_HEADER)ListHead, (::PSLIST_ENTRY)ListEntry);

	RETURN(res);
}

// ******************************************************************
// * 0x00A0 - KfRaiseIrql()
// ******************************************************************
// Raises the hardware priority (irq level)
// NewIrql = Irq level to raise to
// RETURN VALUE previous irq level
XBSYSAPI EXPORTNUM(160) xbox::KIRQL FASTCALL xbox::KfRaiseIrql
(
    IN KIRQL NewIrql
)
{
	LOG_FUNC_ONE_ARG_TYPE(KIRQL_TYPE, NewIrql);

	// Inlined KeGetCurrentIrql() :
	PKPCR Pcr = EmuKeGetPcr();
	KIRQL OldIrql = (KIRQL)Pcr->Irql;

	// Set new before check
	Pcr->Irql = NewIrql;

	if (NewIrql < OldIrql)	{
		Pcr->Irql = 0; // Probably to avoid recursion?
		KeBugCheckEx(IRQL_NOT_GREATER_OR_EQUAL, (PVOID)OldIrql, (PVOID)NewIrql, 0, 0);
	}

	RETURN_TYPE(KIRQL_TYPE, OldIrql);
}

inline int bsr(const uint32_t a) { DWORD result; _BitScanReverse(&result, a); return result; }

// ******************************************************************
// * 0x00A1 - KfLowerIrql()
// ******************************************************************
// Restores the irq level on the current processor
// ARGUMENTS NewIrql = Irql to lower to
XBSYSAPI EXPORTNUM(161) xbox::void_xt FASTCALL xbox::KfLowerIrql
(
    IN KIRQL NewIrql
)
{
	LOG_FUNC_ONE_ARG_TYPE(KIRQL_TYPE, NewIrql);

	KPCR* Pcr = EmuKeGetPcr();

	if (g_bIsDebugKernel && NewIrql > Pcr->Irql) {
		KIRQL OldIrql = Pcr->Irql;
		Pcr->Irql = HIGH_LEVEL; // Probably to avoid recursion?
		KeBugCheckEx(IRQL_NOT_LESS_OR_EQUAL, (PVOID)OldIrql, (PVOID)NewIrql, 0, 0);
		return;
	}

	bool interrupt_flag = DisableInterrupts();
	Pcr->Irql = NewIrql;

	DWORD MaskedInterrupts = HalInterruptRequestRegister & IrqlMasks[NewIrql];

	// Are there any interrupts pending?
	if (MaskedInterrupts > 0) {
		// Determine the highest IRQ level 
		KIRQL HighestIrql = (KIRQL)bsr(MaskedInterrupts);

		// Above dispatch level, clear the highest interrupt request bit
		if (HighestIrql > DISPATCH_LEVEL) {
			HalInterruptRequestRegister ^= 1 << HighestIrql;
		}

		CallSoftwareInterrupt(HighestIrql);
	}

	RestoreInterruptMode(interrupt_flag);
}

// ******************************************************************
// * 0x00A2 - KiBugCheckData
// ******************************************************************
// Source:ReactOS
XBSYSAPI EXPORTNUM(162) xbox::ulong_ptr_xt xbox::KiBugCheckData[5] = { NULL, NULL, NULL, NULL, NULL };

extern xbox::KPRCB *KeGetCurrentPrcb();

// ******************************************************************
// * 0x00A3 - KiUnlockDispatcherDatabase()
// ******************************************************************
XBSYSAPI EXPORTNUM(163) xbox::void_xt FASTCALL xbox::KiUnlockDispatcherDatabase
(
	IN KIRQL OldIrql
)
{
	LOG_FUNC_ONE_ARG_TYPE(KIRQL_TYPE, OldIrql);

	// Wrong, this should only happen when OldIrql >= DISPATCH_LEVEL
	// Checking DpcRoutineActive doesn't work because our Prcb is per-thread instead of being per-processor
	if (!IsDpcActive()) { // Avoid KeIsExecutingDpc(), as that logs
		HalRequestSoftwareInterrupt(DISPATCH_LEVEL);
	}

	if (OldIrql < DISPATCH_LEVEL) {
		// FIXME: this is wrong, it should perform a thread switch and check the kthread of the new selected thread for pending APCs.
		// We can't perform our own threads switching now, so we will just check the current thread

		if (KeGetCurrentThread()->ApcState.KernelApcPending) {
			KiExecuteKernelApc();
		}
	}

	KfLowerIrql(OldIrql);

	LOG_INCOMPLETE(); // TODO : Thread-switch?
}

// ******************************************************************
// * 0x0165 - IdexChannelObject
// ******************************************************************
XBSYSAPI EXPORTNUM(357) xbox::IDE_CHANNEL_OBJECT xbox::IdexChannelObject = { };

// ******************************************************************
// * 0x0169 - RtlSnprintf()
// ******************************************************************
XBSYSAPI EXPORTNUM(361) xbox::int_xt CDECL xbox::RtlSnprintf
(
	IN PCHAR string,
	IN size_xt count,
	IN LPCCH format,
	...
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(string)
		LOG_FUNC_ARG(count)
		LOG_FUNC_ARG(format)
		LOG_FUNC_END;

	// UNTESTED. Possible test-case : debugchannel.xbe

	va_list ap;
	va_start(ap, format);
	INT Result = snprintf(string, count, format, ap);
	va_end(ap);

	RETURN(Result);
}

// ******************************************************************
// * 0x016A - RtlSprintf()
// ******************************************************************
XBSYSAPI EXPORTNUM(362) xbox::int_xt CDECL xbox::RtlSprintf
(
	IN PCHAR string,
	IN LPCCH format,
	...
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(string)
		LOG_FUNC_ARG(format)
		LOG_FUNC_END;

	// UNTESTED. Possible test-case : debugchannel.xbe

	va_list ap;
	va_start(ap, format);
	INT Result = sprintf(string, format, ap);
	va_end(ap);

	RETURN(Result);
}

// ******************************************************************
// * 0x016B - RtlVsnprintf()
// ******************************************************************
XBSYSAPI EXPORTNUM(363) xbox::int_xt CDECL xbox::RtlVsnprintf
(
	IN PCHAR string,
	IN size_xt count,
	IN LPCCH format,
	...
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(string)
		LOG_FUNC_ARG(count)
		LOG_FUNC_ARG(format)
		LOG_FUNC_END;

	// UNTESTED. Possible test-case : debugchannel.xbe

	va_list ap;
	va_start(ap, format);
	INT Result = vsnprintf(string, count, format, ap);
	va_end(ap);

	RETURN(Result);
}

// ******************************************************************
// * 0x016C - RtlVsprintf()
// ******************************************************************
XBSYSAPI EXPORTNUM(364) xbox::int_xt CDECL xbox::RtlVsprintf
(
	IN PCHAR string,
	IN LPCCH format,
	...
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(string)
		LOG_FUNC_ARG(format)
		LOG_FUNC_END;

	// UNTESTED. Possible test-case : debugchannel.xbe

	va_list ap;
	va_start(ap, format);
	INT Result = vsprintf(string, format, ap);
	va_end(ap);

	RETURN(Result);
}

// ******************************************************************
// * 0x016F - UnknownAPI367()
// ******************************************************************
XBSYSAPI EXPORTNUM(367) xbox::ntstatus_xt NTAPI xbox::UnknownAPI367
(
	// UNKNOWN ARGUMENTS
)
{
	LOG_FUNC();

	LOG_UNIMPLEMENTED();

	RETURN(S_OK);
}

// ******************************************************************
// * 0x0170 - UnknownAPI368()
// ******************************************************************
XBSYSAPI EXPORTNUM(368) xbox::ntstatus_xt NTAPI xbox::UnknownAPI368
(
	// UNKNOWN ARGUMENTS
)
{
	LOG_FUNC();

	LOG_UNIMPLEMENTED();

	RETURN(S_OK);
}

// ******************************************************************
// * 0x0171 - UnknownAPI369()
// ******************************************************************
XBSYSAPI EXPORTNUM(369) xbox::ntstatus_xt NTAPI xbox::UnknownAPI369
(
	// UNKNOWN ARGUMENTS
)
{
	LOG_FUNC();

	LOG_UNIMPLEMENTED();

	RETURN(S_OK);
}

// ******************************************************************
// * 0x0172 - XProfpControl()
// ******************************************************************
XBSYSAPI EXPORTNUM(370) xbox::ntstatus_xt NTAPI xbox::XProfpControl // PROFILING
(
	ulong_xt Action,
	ulong_xt Param
)
{
	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(Action)
		LOG_FUNC_ARG(Param)
		LOG_FUNC_END;

	LOG_UNIMPLEMENTED();

	RETURN(S_OK);
}

// ******************************************************************
// * 0x0173 - XProfpGetData()
// ******************************************************************
XBSYSAPI EXPORTNUM(371) xbox::ntstatus_xt NTAPI xbox::XProfpGetData // PROFILING 
(
	// NO ARGUMENTS
)
{
	LOG_FUNC();

	LOG_UNIMPLEMENTED();

	RETURN(S_OK);
}

// ******************************************************************
// * 0x0174 - IrtClientInitFast()
// ******************************************************************
XBSYSAPI EXPORTNUM(372) xbox::ntstatus_xt NTAPI xbox::IrtClientInitFast // PROFILING
(
	// UNKNOWN ARGUMENTS
)
{
	LOG_FUNC();

	LOG_UNIMPLEMENTED();

	RETURN(S_OK);
}

// ******************************************************************
// * 0x0175 - IrtSweep()
// ******************************************************************
XBSYSAPI EXPORTNUM(373) xbox::ntstatus_xt NTAPI xbox::IrtSweep // PROFILING
(
	// UNKNOWN ARGUMENTS
)
{
	LOG_FUNC();

	LOG_UNIMPLEMENTED();

	RETURN(S_OK);
}

// ******************************************************************
// * Handle origin registry (Cxbx-R fork diagnostic)
// ******************************************************************
// A guest thread that waits forever tells us nothing by itself - the handle is a
// bare number. But every handle the guest can wait on left one of our own kernel
// exports, so each of those records what it created. Pairing that with a live
// NtQueryObject/NtQueryEvent probe turns "object=00000698" into a named object with
// a signal state, which is the difference between a hang and a diagnosis.


// The guest is 32-bit MSVC 7 code that keeps a frame pointer, so an EBP chain walk
// gives real callers. _ReturnAddress() alone only reaches the XAPI wrapper
// (CreateEventA, SetEvent, ...) - the interesting frame is always the one above it.
static void CxbxrFormatGuestCallers(void *Ebp, char *Buffer, size_t BufferSize, unsigned MaxFrames)
{
	Buffer[0] = '\0';
	size_t Used = 0;
	struct Frame { Frame *Next; void *Return; };
	const Frame *pFrame = (const Frame *)Ebp;

	for (unsigned i = 0; i < MaxFrames && pFrame != nullptr; ++i) {
		if (::IsBadReadPtr(pFrame, sizeof(Frame))) {
			break;
		}
		void *const Return = pFrame->Return;
		if (Return == nullptr) {
			break;
		}
		// Only guest frames are worth naming: a host module here is our own kernel
		// code on the way in, which the caller already knows about.
		HMODULE hModule = nullptr;
		const bool bHost = ::GetModuleHandleExA(
			GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
			(LPCSTR)Return, &hModule) && hModule != nullptr;
		if (!bHost) {
			const int Written = std::snprintf(Buffer + Used, BufferSize - Used,
				Used == 0 ? "0x%08X" : "<-0x%08X", (unsigned)(uintptr_t)Return);
			if (Written <= 0 || (size_t)Written >= BufferSize - Used) {
				break;
			}
			Used += Written;
		}
		if (pFrame->Next <= pFrame) {
			break; // stacks grow down; anything else is not a frame chain
		}
		pFrame = pFrame->Next;
	}
}

// Turn a kernel export's own frame into the start of an EBP chain. With a standard
// x86 prologue (push ebp; mov ebp,esp) the return address sits at [ebp+4] and the
// caller's saved ebp at [ebp+0], so _AddressOfReturnAddress()[-1] IS the caller's
// frame pointer. Callers must be compiled frame-pointer-full (#pragma optimize("y",off))
// for that to hold; the walk validates every link and prints nothing rather than
// guessing, because a stack walk that invents plausible callers is worse than none -
// this project already lost time to one that did.
void CxbxrDescribeCallers(void *AddressOfReturnAddress, char *Buffer, size_t BufferSize)
{
	void *const CallerEbp = ((void **)AddressOfReturnAddress)[-1];
	// A frame pointer must lie above us on the stack and be pointer-aligned.
	if (CallerEbp <= AddressOfReturnAddress || (((uintptr_t)CallerEbp) & 3) != 0) {
		std::snprintf(Buffer, BufferSize, "<no frame chain>");
		return;
	}
	CxbxrFormatGuestCallers(CallerEbp, Buffer, BufferSize, 8);
	if (Buffer[0] == '\0') {
		std::snprintf(Buffer, BufferSize, "<no guest frames>");
	}
}

static std::mutex g_HandleOriginMtx;
static std::map<void *, std::string> g_HandleOrigin;
// "Nobody ever set it" is a much stronger statement than "it is not set right now" -
// a synchronisation event that was set and consumed reads as unsignalled too.
struct HandleSignalRecord { unsigned Count; std::string LastCallers; };
static std::map<void *, HandleSignalRecord> g_HandleSignalCount;

void CxbxrNoteHandleOrigin(void *Handle, const char *Origin)
{
	if (Handle == nullptr || Origin == nullptr) {
		return;
	}
	std::lock_guard<std::mutex> lock(g_HandleOriginMtx);
	// Handles get recycled, so the newest creator always wins.
	g_HandleOrigin[Handle] = Origin;
}

void CxbxrForgetHandle(void *Handle)
{
	if (Handle == nullptr) {
		return;
	}
	std::lock_guard<std::mutex> lock(g_HandleOriginMtx);
	g_HandleOrigin.erase(Handle);
	g_HandleSignalCount.erase(Handle);
}

void CxbxrNoteHandleSignalled(void *Handle, const char *Callers)
{
	if (Handle == nullptr) {
		return;
	}
	std::lock_guard<std::mutex> lock(g_HandleOriginMtx);
	HandleSignalRecord &Record = g_HandleSignalCount[Handle];
	Record.Count++;
	if (Callers != nullptr) {
		Record.LastCallers = Callers;
	}
}

const char *CxbxrDescribeHandle(void *Handle)
{
	// One buffer per calling thread: the caller prints it immediately, and the only
	// callers are diagnostics that fire at most once every few seconds.
	static thread_local char Description[768];

	std::string Origin;
	std::string LastSignaller;
	unsigned Signals = 0;
	{
		std::lock_guard<std::mutex> lock(g_HandleOriginMtx);
		const auto it = g_HandleOrigin.find(Handle);
		if (it != g_HandleOrigin.end()) {
			Origin = it->second;
		}
		const auto sit = g_HandleSignalCount.find(Handle);
		if (sit != g_HandleSignalCount.end()) {
			Signals = sit->second.Count;
			LastSignaller = sit->second.LastCallers;
		}
	}
	if (Origin.empty()) {
		Origin = "not created by any kernel export we track";
	}

	// Ask the host what the object actually is right now. The type name settles
	// whether we are looking at an event, a thread that never exited, or something
	// else entirely; for events the signal state says whether anyone ever set it.
	char TypeName[64] = "?";
	char State[80] = "";

	// NtQueryObject is not in the emulator's ntdll wrapper set, so resolve it once.
	// ObjectTypeInformation (2) yields an OBJECT_TYPE_INFORMATION whose first member
	// is a UNICODE_STRING naming the type: "Event", "Thread", "Semaphore", ...
	typedef LONG(NTAPI * FPTR_NtQueryObject)(::HANDLE, ULONG, PVOID, ULONG, PULONG);
	static FPTR_NtQueryObject pNtQueryObject = []() -> FPTR_NtQueryObject {
		if (const HMODULE Ntdll = ::GetModuleHandleA("ntdll.dll")) {
			return (FPTR_NtQueryObject)::GetProcAddress(Ntdll, "NtQueryObject");
		}
		return nullptr;
	}();

	if (pNtQueryObject != nullptr) {
		alignas(8) unsigned char Buffer[1024] = {};
		ULONG Returned = 0;
		const LONG Status = pNtQueryObject((::HANDLE)Handle, 2, Buffer, sizeof(Buffer), &Returned);
		if (Status >= 0) {
			// OBJECT_TYPE_INFORMATION opens with a UNICODE_STRING; spell out the layout
			// rather than depending on which windows header happens to be in scope here.
			struct TypeNameString { USHORT Length; USHORT MaximumLength; const wchar_t *Buffer; };
			const auto *Name = reinterpret_cast<const TypeNameString *>(Buffer);
			if (Name->Buffer != nullptr && Name->Length > 0) {
				size_t Chars = Name->Length / sizeof(wchar_t);
				if (Chars > sizeof(TypeName) - 1) {
					Chars = sizeof(TypeName) - 1;
				}
				for (size_t i = 0; i < Chars; ++i) {
					TypeName[i] = (char)Name->Buffer[i];
				}
				TypeName[Chars] = '\0';
			}
		}
		else {
			std::snprintf(TypeName, sizeof(TypeName), "<NtQueryObject 0x%08X>", (unsigned)Status);
		}
	}

	if (std::strcmp(TypeName, "Event") == 0 && NtDll::NtQueryEvent != nullptr) {
		struct { LONG Type; LONG Signalled; } Basic = { -1, -1 };
		ULONG Returned = 0;
		if (NtDll::NtQueryEvent((::HANDLE)Handle, NtDll::EventBasicInformation,
			&Basic, sizeof(Basic), &Returned) >= 0) {
			std::snprintf(State, sizeof(State), " [%s, signalled=%d]",
				Basic.Type == 0 ? "NotificationEvent" : "SynchronizationEvent", (int)Basic.Signalled);
		}
	}
	else if (std::strcmp(TypeName, "Thread") == 0) {
		DWORD ExitCode = 0;
		if (::GetExitCodeThread((::HANDLE)Handle, &ExitCode)) {
			std::snprintf(State, sizeof(State), " [%s]",
				ExitCode == STILL_ACTIVE ? "still running" : "exited");
		}
	}

	std::snprintf(Description, sizeof(Description),
		"%s%s from %s | signalled %u time(s), last by %s",
		TypeName, State, Origin.c_str(), Signals,
		LastSignaller.empty() ? "nobody" : LastSignaller.c_str());
	return Description;
}

// ******************************************************************
// * File read trail (Cxbx-R fork diagnostic)
// ******************************************************************
// The guest is FPO-compiled, so walking its stack only ever reaches XAPI wrappers -
// the game's own functions keep no frame pointer and are invisible to an EBP chain.
// What the loader was READING when it stopped names the subsystem just as well and
// cannot be fooled by calling convention: the last file touched before an infinite
// wait is the thing the wait is about.

static std::mutex g_ReadTrailMtx;
static std::map<void *, std::string> g_OpenFilePath;   // file handle -> path
struct ReadTrailEntry { unsigned Thread; const char *Op; std::string Path; unsigned long long Offset; unsigned Length; };
static ReadTrailEntry g_ReadTrail[256];
static unsigned g_ReadTrailNext = 0;

void CxbxrNoteFileOpened(void *FileHandle, const char *Path)
{
	if (FileHandle == nullptr || Path == nullptr) {
		return;
	}
	std::lock_guard<std::mutex> lock(g_ReadTrailMtx);
	g_OpenFilePath[FileHandle] = Path;
}

void CxbxrNoteFileOp(const char *Op, void *FileHandle, unsigned long long Offset, unsigned Length)
{
	std::lock_guard<std::mutex> lock(g_ReadTrailMtx);
	const auto it = g_OpenFilePath.find(FileHandle);
	ReadTrailEntry &Entry = g_ReadTrail[g_ReadTrailNext % (sizeof(g_ReadTrail) / sizeof(g_ReadTrail[0]))];
	Entry.Thread = (unsigned)::GetCurrentThreadId();
	Entry.Op = Op;
	Entry.Path = (it != g_OpenFilePath.end()) ? it->second : "<unknown handle>";
	Entry.Offset = Offset;
	Entry.Length = Length;
	g_ReadTrailNext++;

	// The wedge always lands immediately after the first npc bundle, so log that
	// phase in full rather than only the tail: comparing a level that loads against
	// one that does not needs the whole sequence, not the last few entries. Nothing
	// else is logged, which keeps this to a few dozen lines per level.
	// Gizzard Gulch (region_02) is larger than Mongo Valley on every axis and loads
	// fine, so this is not about scale - it is something specific in region_03's
	// content. Logging the .lvl reads by offset says how far the parse got before it
	// stopped, which points at the exact record to compare against a level that works.
	if (Entry.Path.find("npc_") != std::string::npos
	 || Entry.Path.find(".lvl") != std::string::npos
	 || Entry.Path.find("character") != std::string::npos
	 || Entry.Path.find("anim") != std::string::npos) {
		printf("NPCIO: thread %u  %-24s %s  offset=%llu len=%u\n",
			Entry.Thread, Op, Entry.Path.c_str(), Offset, Length);
		fflush(stdout);
	}
}

void CxbxrPrintReadTrail(void)
{
	std::lock_guard<std::mutex> lock(g_ReadTrailMtx);
	const unsigned Slots = sizeof(g_ReadTrail) / sizeof(g_ReadTrail[0]);
	const unsigned Count = (g_ReadTrailNext < Slots) ? g_ReadTrailNext : Slots;
	printf("READTRAIL: the last %u file operations before the wedge, oldest first\n", Count);
	for (unsigned i = 0; i < Count; ++i) {
		const ReadTrailEntry &Entry = g_ReadTrail[(g_ReadTrailNext - Count + i) % Slots];
		printf("READTRAIL:   thread %u  %-24s %s  offset=%llu len=%u\n",
			Entry.Thread, Entry.Op ? Entry.Op : "?", Entry.Path.c_str(), Entry.Offset, Entry.Length);
	}
	fflush(stdout);
}

// How many times each path has been opened. Opened once is a load; opened hundreds
// of times is a retry loop getting nowhere, which is what a stalled loader looks
// like from outside.
static std::mutex g_OpenCountMtx;
static std::map<std::string, unsigned> g_OpenCounts;

unsigned CxbxrNoteFileOpenAttempt(const char *Path)
{
	if (Path == nullptr) {
		return 0;
	}
	std::lock_guard<std::mutex> lock(g_OpenCountMtx);
	return ++g_OpenCounts[Path];
}

void CxbxrPrintOpenCounts(void)
{
	std::vector<std::pair<unsigned, std::string>> Sorted;
	{
		std::lock_guard<std::mutex> lock(g_OpenCountMtx);
		for (const auto &Pair : g_OpenCounts) {
			Sorted.emplace_back(Pair.second, Pair.first);
		}
	}
	std::sort(Sorted.begin(), Sorted.end(),
		[](const std::pair<unsigned, std::string> &a, const std::pair<unsigned, std::string> &b) {
			return a.first > b.first;
		});

	printf("OPENCOUNTS: the 12 most-opened paths (a large count means a retry loop)\n");
	for (size_t i = 0; i < Sorted.size() && i < 12; ++i) {
		printf("OPENCOUNTS:   %8u  %s\n", Sorted[i].first, Sorted[i].second.c_str());
	}
	fflush(stdout);
}

// ******************************************************************
// * Guest thread census (Cxbx-R fork diagnostic)
// ******************************************************************
// The blocked thread turned out to be the engine's async I/O worker, which is
// SUPPOSED to sit in an infinite wait when there is nothing to do. That makes the
// interesting thread the one that stopped queuing work - and to find it, every
// guest thread has to be identifiable and its liveness known. Start addresses are
// guest VAs the title's PDB resolves to real engine function names.

struct GuestThreadRecord { unsigned StartAddress; unsigned Context; bool Exited; unsigned ExitStatus; };
static std::mutex g_ThreadCensusMtx;
static std::map<unsigned, GuestThreadRecord> g_ThreadCensus;

void CxbxrNoteThreadStarted(unsigned ThreadId, unsigned StartAddress, unsigned Context)
{
	std::lock_guard<std::mutex> lock(g_ThreadCensusMtx);
	g_ThreadCensus[ThreadId] = GuestThreadRecord{ StartAddress, Context, false, 0 };
}

void CxbxrNoteThreadExited(unsigned ThreadId, unsigned ExitStatus)
{
	std::lock_guard<std::mutex> lock(g_ThreadCensusMtx);
	const auto it = g_ThreadCensus.find(ThreadId);
	if (it != g_ThreadCensus.end()) {
		it->second.Exited = true;
		it->second.ExitStatus = ExitStatus;
	}
}

void CxbxrPrintThreadCensus(void)
{
	std::lock_guard<std::mutex> lock(g_ThreadCensusMtx);
	printf("THREADS: every guest thread created, and whether it is still running\n");
	for (const auto &Pair : g_ThreadCensus) {
		// A thread can be gone without having called PsTerminateSystemThread, so ask
		// the OS rather than trusting the exit hook alone.
		const char *Liveness = "unknown";
		if (const HANDLE hThread = ::OpenThread(THREAD_QUERY_LIMITED_INFORMATION, FALSE, Pair.first)) {
			DWORD ExitCode = 0;
			if (::GetExitCodeThread(hThread, &ExitCode)) {
				Liveness = (ExitCode == STILL_ACTIVE) ? "RUNNING" : "DEAD";
			}
			::CloseHandle(hThread);
		}
		else {
			Liveness = "DEAD (no handle)";
		}
		printf("THREADS:   tid=%-6u start=0x%08X context=0x%08X  %-16s %s\n",
			Pair.first, Pair.second.StartAddress, Pair.second.Context, Liveness,
			Pair.second.Exited ? "(exited cleanly)" : "");
	}
	fflush(stdout);
}
