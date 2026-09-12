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
// *  (c) 2026 Stranger's Wrath beta research (Cxbx-Reloaded fork)
// *
// *  All rights reserved
// *
// ******************************************************************
#pragma once

// The Xbox DirectSound 3D calculator's output, received per voice.
//
// On this title the calculator (light HRTF, 5 channel) is pure CPU code inside the
// XBE and runs natively; XACT hands its result to DSOUND with
// IDirectSoundBuffer_Set3DVoiceData / IDirectSoundStream_Set3DVoiceData. This
// header holds the guest structure, the per-voice merged copy the emulator keeps,
// and the fold of that copy onto what a host stereo buffer can express: a volume
// offset, a pan and a pitch delta. Layouts and units: swse/research/
// DS3D_CALCULATOR_CONTRACT.md 2.4 and DS3D_MATH.md 2.3 / 6.2 / 7.
//
// Include at file scope only (it opens namespace xbox itself).

#include <windows.h>    // LONG / DWORD
#include "xbox_types.h" // xbox::dword_xt / xbox::long_xt

namespace xbox {

// _DS3DCALCVOICEDATA (0x38 bytes): the calculator's output / a voice's 3D settings.
// Volumes in mB (hundredths of dB), <= 0; 0 = no attenuation, -10000 = DSBVOLUME_MIN.
// dwChangeMask has one bit per row; the calculator sets a bit only when the value
// changed, and CDirectSoundVoice::Set3DVoiceData (0x1d223f) copies only flagged fields.
struct X_DS3DCALCVOICEDATA {
    dword_xt dwChangeMask;          // +0x00
    long_xt  lDistanceVolume;       // +0x04  bit 0x01
    long_xt  lConeVolume;           // +0x08  bit 0x02
    long_xt  lFrontVolume;          // +0x0C  bit 0x04  level of the front 3D pair (mixbins 6/7)
    long_xt  lRearVolume;           // +0x10  bit 0x04  level of the rear 3D pair (mixbins 8/9)
    long_xt  lLeftRightVolume;      // +0x14  bit 0x08  extra level on the front pair vs centre
    long_xt  lCenterVolume;         // +0x18  bit 0x08  level of mixbin 2
    long_xt  lDirectVolume;         // +0x1C  bit 0x10  I3DL2 direct path (added to every 3D bin)
    long_xt  lReverbVolume;         // +0x20  bit 0x10  I3DL2 send (mixbin 10)
    long_xt  lDopplerPitch;         // +0x24  bit 0x20  1/4096 octave, the units of SetPitch
    float    flFIRFilterAzimuth;    // +0x28  bit 0x40  degrees, (-180,180], + = right of the listener
    float    flFIRFilterElevation;  // +0x2C  bit 0x40  degrees (always 0 with the light HRTF)
    dword_xt dwIIRFilterDirect;     // +0x30  bit 0x80  MCPX 1-pole low-pass coefficient word
    dword_xt dwIIRFilterReverb;     // +0x34  bit 0x80
};
static_assert(sizeof(X_DS3DCALCVOICEDATA) == 0x38, "X_DS3DCALCVOICEDATA must match the Xbox _DS3DCALCVOICEDATA layout (0x38 bytes)");

// dwChangeMask bits (the same numbering is used by Cxbxr3DVoiceState::dwHave).
// XACT keeps only 0xDC (front/rear, centre, I3DL2, FIR, IIR) for the MIXIN parent
// voice of a positional effect and only 0x33 (distance, cone, I3DL2, doppler) for
// the audible track voice routed into it (DS3D_CALCULATOR_CONTRACT.md 3.3).
enum : dword_xt {
    X_DS3DVOICEDATA_DISTANCE  = 0x01, // lDistanceVolume
    X_DS3DVOICEDATA_CONE      = 0x02, // lConeVolume
    X_DS3DVOICEDATA_FRONTREAR = 0x04, // lFrontVolume, lRearVolume
    X_DS3DVOICEDATA_CENTER    = 0x08, // lLeftRightVolume, lCenterVolume
    X_DS3DVOICEDATA_I3DL2     = 0x10, // lDirectVolume, lReverbVolume
    X_DS3DVOICEDATA_DOPPLER   = 0x20, // lDopplerPitch
    X_DS3DVOICEDATA_FIRFILTER = 0x40, // flFIRFilterAzimuth, flFIRFilterElevation
    X_DS3DVOICEDATA_IIRFILTER = 0x80, // dwIIRFilterDirect, dwIIRFilterReverb
    X_DS3DVOICEDATA_ALL       = 0xFF,
};

} // namespace xbox

// The voice data merged so far for one hybrid buffer / stream (EmuDirectSoundBuffer::Xb_3D,
// X_CDirectSoundStream::Xb_3D). Fields never received stay 0, which is "no effect".
struct Cxbxr3DVoiceState {
    LONG lDistanceVolume, lConeVolume, lFrontVolume, lRearVolume,
         lLeftRightVolume, lCenterVolume, lDirectVolume, lReverbVolume, lDopplerPitch;
    float flAzimuth, flElevation;
    DWORD dwHave;   // bits received so far (same numbering as dwChangeMask)
    bool  bUse;     // Use3DVoiceData(TRUE)
};

// Zero everything, bUse = false.
void Cxbxr3DVoice_Init(Cxbxr3DVoiceState&);
// Copy each field whose bit is set in pData->dwChangeMask, and record the bit in dwHave.
// A null pData is ignored; the caller checks readability.
void Cxbxr3DVoice_Merge(Cxbxr3DVoiceState&, const xbox::X_DS3DCALCVOICEDATA*);
// clamp(lDistanceVolume + lConeVolume + lDirectVolume, -10000, 0): the attenuation the
// Xbox adds to every speaker bin of the voice (ConvertVolumeValues). <= 0; 0 unless bUse.
LONG Cxbxr3DVoice_VolumeOffsetMb(const Cxbxr3DVoiceState&);
// Host pan (DirectSound units, -10000..10000, > 0 attenuates the LEFT channel) derived
// from flFIRFilterAzimuth: sin(azimuth) * 10000 * kPanScale. 0 unless bUse and the
// FIR bit (0x40) has been received.
LONG Cxbxr3DVoice_PanMb(const Cxbxr3DVoiceState&);
// lDopplerPitch in 1/4096 octave, to add on top of the voice's own pitch. 0 unless bUse
// and the doppler bit (0x20) has been received.
LONG Cxbxr3DVoice_PitchDelta(const Cxbxr3DVoiceState&);
