"""The engine's name hash - recovered by disassembling the beta's own code.

NOT guessed. `Steef.pdb` names the routines; the bytes were read out of
`Steef.exe` at the addresses the PDB gives:

    CRC::Reset               rva 0x365165   83 09 ff c3
                             -> or dword [ecx], -1 ; ret        (init 0xFFFFFFFF)

    CRC::GetHash             rva 0x3652f9   8b 01 c3
                             -> mov eax,[ecx] ; ret             (NO final inversion)

    CRC::AddB                rva 0x365169
                             -> crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
                                with the standard IEEE reflected table, verified
                                byte-for-byte against CRC32Table @ rva 0x757b40

    CRC::AddStringSensitive  rva 0x3652d0
                                 xor edi,edi                ; edi = length
                             loop:
                                 movsx eax,al / push eax
                                 inc esi / call AddB / inc edi
                                 mov al,[esi] / test al,al / jnz loop
                                 push edi / call AddB       ; <-- THE LENGTH,
                                 ret 4                      ;     as a final byte

    CRC::AddStringInsensitive rva 0x365319
                             same, but each char goes through tolower first and
                             '/' is rewritten to '\\' before hashing. That is the
                             PATH form.

So the hash is an ordinary reflected CRC-32 (init 0xFFFFFFFF, no final XOR)
over the characters, followed by ONE extra byte holding the string length.
The trailing length byte is why every off-the-shelf CRC32 variant failed to
reproduce it, and why per-byte contributions looked inconsistent across
different string lengths - the length participates in the checksum.

Self-check: for the empty string this reduces to the CRC of a single 0x00 byte,
giving 0x2DFD1072, which matches the value measured independently from the
level files.
"""
import zlib


def name_hash(s) -> int:
    """CRC::AddStringSensitive - exact names, case preserved."""
    b = s.encode('ascii') if isinstance(s, str) else bytes(s)
    return zlib.crc32(b + bytes([len(b) & 0xFF])) ^ 0xFFFFFFFF


def path_hash(s) -> int:
    """CRC::AddStringInsensitive - lowercased, forward slashes become back."""
    b = s.encode('ascii') if isinstance(s, str) else bytes(s)
    b = b.lower().replace(b'/', b'\\')
    return zlib.crc32(b + bytes([len(b) & 0xFF])) ^ 0xFFFFFFFF


def tag_hash(s) -> int:
    """The case-folded form the .lvl object graph actually uses: UPPERCASE.

    Same CRC, same trailing length byte - but the case fold is toupper, not
    the tolower that `path_hash` applies. Established from the level data, not
    from the disassembly:

        tag_hash('PipeTag')  == 0x969E0F57  == the v6 class id of every
                                               PipeTag node (104 of them)
        tag_hash('pipe')     == 0x010D8177  == the v6 name slot on all of them
                                               (every v5 PipeTag is named 'pipe')
        tag_hash('WorldTag') == 0x1A0FF55D  == the class id in every v6 root
        tag_hash('')         == 0x2DFD1072  == the empty-name value, and the
                                               root's own m_tagName

    Scale check: uppercase-hashing the 46,994 v5 instance names finds 23,721 of
    them (50.5%) in the matching v6 region file - the beta/release content
    overlap - and the class ids those land on agree with the class-name hash in
    28 of 28 cases. The lowercase form matches nothing (2 of 46,994, i.e.
    chance). 29 of the 31 observed v6 class ids are bare class names under this
    function; the last two are classes the beta did not have
    (RadarLocation::Tag, WeatherVolumeTag).

    Note the input is the BARE class name - 'PipeTag', 'SpawnPoint::Tag' - not
    the MSVC typeid string, so strip the 'class '/'struct ' prefix that v5
    stores. The '::' is kept.
    """
    b = s.encode('ascii') if isinstance(s, str) else bytes(s)
    b = b.upper()
    return zlib.crc32(b + bytes([len(b) & 0xFF])) ^ 0xFFFFFFFF


EMPTY_STRING_HASH = 0x2DFD1072      # the engine's own c_emptyStringHash

if __name__ == '__main__':
    assert name_hash('') == EMPTY_STRING_HASH, 'empty-string self-check failed'
    print('empty        ', hex(name_hash('')))
    for s in ('pipe', 'PipeTag', 'SpawnPoint::Tag', 'test2_Watermesh_1'):
        print(f'{s:20} sensitive {name_hash(s):#010x}   insensitive {path_hash(s):#010x}')
