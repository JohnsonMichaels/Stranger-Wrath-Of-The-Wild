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
// *  (c) 2018 Patrick van Logchem <pvanlogchem@gmail.com>
// *
// *  All rights reserved
// *
// ******************************************************************
#pragma once

#include <cstdint>

/*! xbdm success code (FACILITY_XBDM, severity 0) - what every Dm* API returns
    when it worked. Titles compare the returned HRESULT against this, so a stub
    that returns void (leaving EAX undefined) reads as a random failure code.
    Spelled as a long rather than HRESULT so this header stays usable without
    windows.h. */
#define XBDM_NOERR ((long)0x02DB0000L)

/*! "no more items" - returned by the Dm*Walk* enumerators once the list is
    exhausted. What matters to a caller is that it is a failure code, so the
    enumeration loop stops instead of reading an output record we never wrote. */
#define XBDM_ENDOFLIST ((long)0x82DB0104L)

/*! Page counts reported by DmQueryMemoryStatistics (xbdm ordinal 75).

    cbSize is 0x30, i.e. a size field plus eleven page counters. The field order
    below is not guesswork: Oddworld: Stranger's Wrath (2004 beta) reads offset
    0x1C for what its own PDB names MemorySystem::GetVirtualMappedBytes and
    offset 0x28 for MemorySystem::GetContiguousBytes, and both land exactly where
    this layout puts VirtualMappedPages and ContiguousPages. Every counter is in
    pages; callers shift left by PAGE_SHIFT to get bytes. */
typedef struct _DM_MEMORY_STATISTICS
{
    uint32_t cbSize;                    // 0x00 - caller sets this to sizeof(DM_MEMORY_STATISTICS)
    uint32_t TotalPages;                // 0x04
    uint32_t AvailablePages;            // 0x08
    uint32_t StackPages;                // 0x0C
    uint32_t VirtualPageTablePages;     // 0x10
    uint32_t SystemPageTablePages;      // 0x14
    uint32_t PoolPages;                 // 0x18
    uint32_t VirtualMappedPages;        // 0x1C
    uint32_t ImagePages;                // 0x20
    uint32_t FileCachePages;            // 0x24
    uint32_t ContiguousPages;           // 0x28
    uint32_t DebuggerPages;             // 0x2C
} DM_MEMORY_STATISTICS, *PDM_MEMORY_STATISTICS;

/*! Highest xbdm ordinal Cxbx knows about. The table must cover every ordinal a
    title can import: MapThunkTable indexes it with the ordinal straight out of
    the XBE, so a table that stops short means an out-of-bounds read whose result
    is written into the title's own import slot and later called. All three 2004
    Stranger's Wrath builds import ordinal 75 (DmQueryMemoryStatistics), which is
    what used to happen when this table stopped at 72. */
#define CXBX_XBDM_MAX_ORDINAL 75

/*! xbdm thunk table */
extern uint32_t Cxbx_LibXbdmThunkTable[1 + CXBX_XBDM_MAX_ORDINAL];

/*! Installed by the emulation process so DmQueryMemoryStatistics can reach the
    memory manager. See the note in CxbxXbdm.cpp for why this is a hook. */
extern void (*g_pfnXbdmQueryMemoryStatistics)(PDM_MEMORY_STATISTICS);
