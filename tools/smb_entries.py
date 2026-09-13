"""Walk a beta .smb prefs bundle as: header + N entries of
   [u32 stamp][u32 07][u32 id][u32 0][u32 0][u32 0x410]
   [u32 pathLen][path][u32 clsLen][cls][records...][u32 0]
   record = [u32 size incl][u32 nameHash][u32 typeHash][payload size-12]
"""
import sys, struct

def parse(P):
    D = open(P, 'rb').read()
    u32 = lambda o: struct.unpack_from('<I', D, o)[0]
    magic, ver, pl = struct.unpack_from('<III', D, 0)
    assert magic == 0x3A4B5C6D
    self_path = D[12:12 + pl].decode('latin1')
    o = 12 + pl
    A, B, C, Dd, E, F, G, H = struct.unpack_from('<8I', D, o)
    o += 32
    assert u32(o) == 0xBEEF1234
    o += 4
    entries = []
    while o < B - 4:
        hdr_off = o
        stamp = u32(o); f1 = u32(o + 4); eid = u32(o + 8)
        z1 = u32(o + 12); z2 = u32(o + 16); f2 = u32(o + 20)
        o += 24
        plen = u32(o); o += 4
        path = D[o:o + plen].decode('latin1'); o += plen
        clen = u32(o); o += 4
        cls = D[o:o + clen].decode('latin1'); o += clen
        recs = []
        while True:
            sz = u32(o)
            if sz == 0:
                o += 4
                break
            if sz < 12 or o + sz > B:
                raise RuntimeError('bad rec sz %d at %#x (entry %s)' % (sz, o, path))
            recs.append((o, sz, u32(o + 4), u32(o + 8), D[o + 12:o + sz]))
            o += sz
        entries.append(dict(hdr=hdr_off, stamp=stamp, f1=f1, eid=eid, z1=z1, z2=z2,
                            f2=f2, path=path, cls=cls, recs=recs, end=o))
    return D, dict(ver=ver, self_path=self_path, A=A, B=B, C=C, D=Dd, E=E, F=F, G=G, H=H), entries

if __name__ == '__main__':
    P = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\<you>\SWBeta\Game\data\global\global_prefs.smb'
    D, hdr, ents = parse(P)
    print('%s  G=%d parsed=%d  B=%#x last_end=%#x  size=%d' %
          (hdr['self_path'], hdr['G'], len(ents), hdr['B'], ents[-1]['end'], len(D)))
    for i, e in enumerate(ents):
        print('%3d %08X f1=%d f2=%#x %-70s | %-32s recs=%d' %
              (i, e['eid'], e['f1'], e['f2'], e['path'], e['cls'], len(e['recs'])))
