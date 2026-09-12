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
#ifndef EMUKRNL_H
#define EMUKRNL_H

#include "core\kernel\init\CxbxKrnl.h"
#include "core\kernel\support\Emu.h"
#include "core\kernel\support\EmuFS.h"
#include "EmuKrnlKi.h"
#include <future>

// CONTAINING_RECORD macro
// Gets the value of structure member (field - num1),given the type(MYSTRUCT, in this code) and the List_Entry head(temp, in this code)
// See https://stackoverflow.com/questions/8240273/a-portable-way-to-calculate-pointer-to-the-whole-structure-using-pointer-to-a-fi
//#define CONTAINING_RECORD(ptr, type, field) \
//	(((type) *)((char *)(ptr) - offsetof((type), member)))

#define OBJECT_TO_OBJECT_HEADER(Object) \
    CONTAINING_RECORD(Object, OBJECT_HEADER, Body)

void InitializeListHead(xbox::PLIST_ENTRY pListHead);
bool IsListEmpty(xbox::PLIST_ENTRY pListHead);
void InsertHeadList(xbox::PLIST_ENTRY pListHead, xbox::PLIST_ENTRY pEntry);
void InsertTailList(xbox::PLIST_ENTRY pListHead, xbox::PLIST_ENTRY pEntry);
//#define RemoveEntryList(e) do { PLIST_ENTRY f = (e)->Flink, b = (e)->Blink; f->Blink = b; b->Flink = f; (e)->Flink = (e)->Blink = NULL; } while (0)

xbox::boolean_xt RemoveEntryList(xbox::PLIST_ENTRY pEntry);
xbox::PLIST_ENTRY RemoveHeadList(xbox::PLIST_ENTRY pListHead);
xbox::PLIST_ENTRY RemoveTailList(xbox::PLIST_ENTRY pListHead);

extern xbox::LAUNCH_DATA_PAGE DefaultLaunchDataPage;
extern xbox::PKINTERRUPT EmuInterruptList[MAX_BUS_INTERRUPT_LEVEL + 1];
// Indicates to disable/enable all interrupts when cli and sti instructions are executed
inline std::atomic_bool g_bEnableAllInterrupts = true;

class HalSystemInterrupt {
public:
	void Assert(bool state) {
		// If the interrupt was marked as Asserted, and was previously not, set the pending flag too!
		if (m_Asserted == 0 && state == 1) {
			m_Pending = true;
		}

		m_Asserted = state;
	};

	void Enable() {
		m_Enabled = true;
	}

	void Disable() {
		m_Enabled = false;
	}

	bool IsEnabled() {
		return m_Enabled;
	}

	bool IsPending() {
		return m_Asserted && m_Pending;
	}

	void SetInterruptMode(xbox::KINTERRUPT_MODE InterruptMode) {
		m_InterruptMode = InterruptMode;
	}

	void Trigger(xbox::PKINTERRUPT Interrupt) {
		// If interrupt was level sensitive, we clear the pending flag, preventing the interrupt from being triggered 
		// until it is deasserted then asserted again. Latched interrupts are triggered until the line is Deasserted!
		if (m_InterruptMode == xbox::KINTERRUPT_MODE::LevelSensitive) {
			m_Pending = false;
		}

		xbox::boolean_xt(__stdcall *ServiceRoutine)(xbox::PKINTERRUPT, void*) = (xbox::boolean_xt(__stdcall *)(xbox::PKINTERRUPT, void*))Interrupt->ServiceRoutine;
		xbox::boolean_xt result = ServiceRoutine(Interrupt, Interrupt->ServiceContext);
	}
private:
	bool m_Asserted = false;
	bool m_Enabled = false;
	xbox::KINTERRUPT_MODE m_InterruptMode;
	bool m_Pending = false;
};

extern HalSystemInterrupt HalSystemInterrupts[MAX_BUS_INTERRUPT_LEVEL + 1];

bool DisableInterrupts();
void RestoreInterruptMode(bool value);
void CallSoftwareInterrupt(const xbox::KIRQL SoftwareIrql);
bool AddWaitObject(xbox::PKTHREAD kThread, xbox::PLARGE_INTEGER Timeout);

template<typename T>
std::optional<xbox::ntstatus_xt> SatisfyWait(T &&Lambda, xbox::PKTHREAD kThread, xbox::boolean_xt Alertable, xbox::char_xt WaitMode)
{
	if (const auto ret = Lambda(kThread)) {
		return ret;
	}

	xbox::KiApcListMtx.lock();
	bool EmptyKernel = IsListEmpty(&kThread->ApcState.ApcListHead[xbox::KernelMode]);
	bool EmptyUser = IsListEmpty(&kThread->ApcState.ApcListHead[xbox::UserMode]);
	xbox::KiApcListMtx.unlock();

	if (EmptyKernel == false) {
		xbox::KiExecuteKernelApc();
	}

	if ((EmptyUser == false) &&
		(Alertable == TRUE) &&
		(WaitMode == xbox::UserMode)) {
		xbox::KiExecuteUserApc();
		xbox::KiUnwaitThreadAndLock(kThread, X_STATUS_USER_APC, 0);
		return kThread->WaitStatus;
	}

	return std::nullopt;
}

// What the current thread is waiting on, recorded by NtWaitForMultipleObjectsEx so
// the infinite-wait diagnostic in WaitApc can NAME the object rather than only
// reporting that something is stuck.
inline thread_local void *g_WaitDiagObject = nullptr;   // guest handle
inline thread_local void *g_WaitDiagNative = nullptr;   // host handle after conversion
inline thread_local bool g_WaitDiagIsOb = false;        // set when the guest handle was an ob handle
inline thread_local unsigned g_WaitDiagCount = 0;
inline thread_local char g_WaitDiagCallers[160] = {};  // guest frames above the wait

// Knowing that a wait is stuck is only half an answer - the other half is WHICH
// object, and every handle the guest can wait on came out of a kernel export we
// control. Each of those records where it handed the handle out, so a bare number
// like 0x698 turns back into "Event(Synchronization,initial=0) NtCreateEvent".
void CxbxrNoteHandleOrigin(void *Handle, const char *Origin);
void CxbxrForgetHandle(void *Handle);
void CxbxrNoteHandleSignalled(void *Handle, const char *Callers);
// Pass _AddressOfReturnAddress(); formats the guest EBP chain above the caller.
void CxbxrDescribeCallers(void *AddressOfReturnAddress, char *Buffer, size_t BufferSize);
const char *CxbxrDescribeHandle(void *Handle); // never null

// Where the loader had got to when it stopped. Guest code is FPO-compiled, so its
// stack cannot be walked; the file it was reading identifies the subsystem instead.
void CxbxrNoteFileOpened(void *FileHandle, const char *Path);
void CxbxrNoteFileOp(const char *Op, void *FileHandle, unsigned long long Offset, unsigned Length);
void CxbxrPrintReadTrail(void);
unsigned CxbxrNoteFileOpenAttempt(const char *Path); // returns the new count
void CxbxrPrintOpenCounts(void);
// Which guest threads exist and which have died - the thread that stopped queuing
// work is the one to find, and a dead thread cannot queue anything.
void CxbxrNoteThreadStarted(unsigned ThreadId, unsigned StartAddress, unsigned Context);
void CxbxrNoteThreadExited(unsigned ThreadId, unsigned ExitStatus);
void CxbxrPrintThreadCensus(void);

template<bool host_wait, typename T>
xbox::ntstatus_xt WaitApc(T &&Lambda, xbox::PLARGE_INTEGER Timeout, xbox::boolean_xt Alertable, xbox::char_xt WaitMode, xbox::PKTHREAD kThread)
{
	// NOTE1: kThread->Alerted is currently never set. When the alerted mechanism is implemented, the alerts should
	// also interrupt the wait.

	xbox::ntstatus_xt status;
	if (Timeout == nullptr) {
		// No timout specified, so this is an infinite wait until an alert, a user apc or the object(s) become(s) signalled
		//
		// A guest thread parked here forever is how loading region_03 (Mongo Valley)
		// wedges: the game calls WaitForSingleObject on an object that is never
		// signalled, so the loader stops while the renderer keeps drawing the loading
		// screen. Nothing errors and nothing crashes - it simply waits. Report it, so
		// an unsatisfiable wait names itself instead of looking like a hang.
		unsigned long long Spins = 0;
		const DWORD WaitStartTick = GetTickCount();
		while (true) {
			if (const auto ret = SatisfyWait(Lambda, kThread, Alertable, WaitMode)) {
				status = *ret;
				break;
			}

			// ~1s, then every ~5s. Cheap: only a stuck wait ever reaches the print.
			if ((++Spins & 0xFFFFF) == 0) {
				const DWORD Waited = GetTickCount() - WaitStartTick;
				if (Waited > 1000) {
					static thread_local DWORD s_LastReport = 0;
					if (Waited - s_LastReport > 5000) {
						s_LastReport = Waited;
						printf("WAIT: thread %u blocked %u ms in an INFINITE wait "
							"(alertable=%d waitMode=%d) object=%p count=%u -- %s%s\n",
							(unsigned)GetCurrentThreadId(), (unsigned)Waited,
							(int)Alertable, (int)WaitMode, g_WaitDiagObject, g_WaitDiagCount,
							g_WaitDiagIsOb ? "ob handle -> " : "",
							CxbxrDescribeHandle(g_WaitDiagIsOb ? g_WaitDiagNative : g_WaitDiagObject));
						printf("WAIT:   waiter called from %s\n", g_WaitDiagCallers);
						fflush(stdout);
					}
				}
			}

			std::this_thread::yield();
		}
	}
	else if (Timeout->QuadPart == 0) {
		assert(host_wait);
		// A zero timeout means that we only have to check the conditions once and then return immediately if they are not satisfied
		if (const auto ret = SatisfyWait(Lambda, kThread, Alertable, WaitMode)) {
			status = *ret;
		}
		else {
			// If the wait failed, then always remove the wait block. Note that this can only happen with host waits, since guest waits never call us at all
			// when Timeout->QuadPart == 0. Test case: Halo 2 (sporadically when playing the intro video)
			xbox::KiUnwaitThreadAndLock(kThread, X_STATUS_TIMEOUT, 0);
			status = kThread->WaitStatus;
		}
	}
	else {
		// A non-zero timeout means we have to check the conditions until we reach the requested time
		while (true) {
			if (const auto ret = SatisfyWait(Lambda, kThread, Alertable, WaitMode)) {
				status = *ret;
				break;
			}

			if (host_wait && (kThread->State == xbox::Ready)) {
				status = kThread->WaitStatus;
				break;
			}

			std::this_thread::yield();
		}
	}

	if constexpr (host_wait) {
		kThread->State = xbox::Running;
	}
	return status;
}

#endif
