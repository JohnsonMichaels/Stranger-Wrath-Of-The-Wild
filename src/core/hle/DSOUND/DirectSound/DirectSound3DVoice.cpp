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
// *  (c) 2026 Stranger's Wrath beta research (Cxbx-Reloaded fork)
// *
// *  All rights reserved
// *
// ******************************************************************

#include "DirectSound3DVoice.hpp"

#include <cmath>

namespace {

// Host pan scale. The Xbox lateralises a 3D voice with a per-azimuth HRTF FIR pair whose
// measured interaural level difference is only ~3 dB at 90 degrees (DS3D_MATH.md 5.6); a
// full sin(azimuth) pan on a stereo host would be far wider than the console, so the
// pan is scaled down. Single tuning knob for the whole 3D path.
constexpr float kPanScale = 0.5f;

constexpr float kDegToRad = 3.14159265358979323846f / 180.0f;

constexpr LONG kVolumeMinMb = -10000; // DSBVOLUME_MIN
constexpr LONG kPanMaxMb    =  10000; // DSBPAN_RIGHT
// Doppler pitch range the Xbox calculator can produce (DS3D_MATH.md 5.5).
constexpr LONG kDopplerMin  = -32767;
constexpr LONG kDopplerMax  =   4096;

inline LONG ClampLong(long long value, long long lo, long long hi)
{
    if (value < lo) { return static_cast<LONG>(lo); }
    if (value > hi) { return static_cast<LONG>(hi); }
    return static_cast<LONG>(value);
}

} // namespace

void Cxbxr3DVoice_Init(Cxbxr3DVoiceState& state)
{
    state.lDistanceVolume  = 0;
    state.lConeVolume      = 0;
    state.lFrontVolume     = 0;
    state.lRearVolume      = 0;
    state.lLeftRightVolume = 0;
    state.lCenterVolume    = 0;
    state.lDirectVolume    = 0;
    state.lReverbVolume    = 0;
    state.lDopplerPitch    = 0;
    state.flAzimuth        = 0.0f;
    state.flElevation      = 0.0f;
    state.dwHave           = 0;
    state.bUse             = false;
}

// Same rule as CDirectSoundVoice::Set3DVoiceData (0x1d2265..0x1d234d): a field is copied
// only when its bit is set. The calculator never clears bits it did not set, so a field
// that did not change keeps the value received earlier.
void Cxbxr3DVoice_Merge(Cxbxr3DVoiceState& state, const xbox::X_DS3DCALCVOICEDATA* pData)
{
    if (pData == nullptr) {
        return;
    }
    const DWORD mask = pData->dwChangeMask & xbox::X_DS3DVOICEDATA_ALL;

    if (mask & xbox::X_DS3DVOICEDATA_DISTANCE) {
        state.lDistanceVolume = static_cast<LONG>(pData->lDistanceVolume);
    }
    if (mask & xbox::X_DS3DVOICEDATA_CONE) {
        state.lConeVolume = static_cast<LONG>(pData->lConeVolume);
    }
    if (mask & xbox::X_DS3DVOICEDATA_FRONTREAR) {
        state.lFrontVolume = static_cast<LONG>(pData->lFrontVolume);
        state.lRearVolume  = static_cast<LONG>(pData->lRearVolume);
    }
    if (mask & xbox::X_DS3DVOICEDATA_CENTER) {
        state.lLeftRightVolume = static_cast<LONG>(pData->lLeftRightVolume);
        state.lCenterVolume    = static_cast<LONG>(pData->lCenterVolume);
    }
    if (mask & xbox::X_DS3DVOICEDATA_I3DL2) {
        state.lDirectVolume = static_cast<LONG>(pData->lDirectVolume);
        state.lReverbVolume = static_cast<LONG>(pData->lReverbVolume);
    }
    if (mask & xbox::X_DS3DVOICEDATA_DOPPLER) {
        state.lDopplerPitch = static_cast<LONG>(pData->lDopplerPitch);
    }
    if (mask & xbox::X_DS3DVOICEDATA_FIRFILTER) {
        state.flAzimuth   = pData->flFIRFilterAzimuth;
        state.flElevation = pData->flFIRFilterElevation;
    }
    // X_DS3DVOICEDATA_IIRFILTER: the MCPX low-pass words have no host equivalent; only the
    // bit is recorded.

    state.dwHave |= mask;
}

// Xbox ConvertVolumeValues (0x1d8220): base = clamp(cone + distance) lands on every speaker
// bin, and the I3DL2 direct level is added on the 3D pairs the voice actually feeds. The
// host has one volume, so the three are folded into one offset. Fields not received are 0.
LONG Cxbxr3DVoice_VolumeOffsetMb(const Cxbxr3DVoiceState& state)
{
    if (!state.bUse) {
        return 0;
    }
    const long long sum = static_cast<long long>(state.lDistanceVolume)
                        + static_cast<long long>(state.lConeVolume)
                        + static_cast<long long>(state.lDirectVolume);
    return ClampLong(sum, kVolumeMinMb, 0);
}

// On the Xbox left/right of each 3D pair carry EQUAL volumes and the placement is the HRTF
// FIR pair chosen by the azimuth (+ = right). DirectSound's pan is > 0 to attenuate the
// left channel, i.e. to move the sound right, so sin(azimuth) has the right sign directly.
// Rear azimuths fold onto the same pan (sin(180 - a) == sin(a)): a stereo host has no rear.
LONG Cxbxr3DVoice_PanMb(const Cxbxr3DVoiceState& state)
{
    if (!state.bUse || (state.dwHave & xbox::X_DS3DVOICEDATA_FIRFILTER) == 0) {
        return 0;
    }
    const float pan = std::sin(state.flAzimuth * kDegToRad) * 10000.0f * kPanScale;
    if (std::isnan(pan)) {
        return 0; // a garbage azimuth must not become a hard pan
    }
    return ClampLong(static_cast<long long>(pan), -kPanMaxMb, kPanMaxMb);
}

LONG Cxbxr3DVoice_PitchDelta(const Cxbxr3DVoiceState& state)
{
    if (!state.bUse || (state.dwHave & xbox::X_DS3DVOICEDATA_DOPPLER) == 0) {
        return 0;
    }
    return ClampLong(state.lDopplerPitch, kDopplerMin, kDopplerMax);
}
