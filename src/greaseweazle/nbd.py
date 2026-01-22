# greaseweazle/nbd.py
#
# Greaseweazle control script: Read Disk to Image.
#
# Written & released by Keir Fraser <keir.xen@gmail.com>
#
# This is free and unencumbered software released into the public domain.
# See the file COPYING for more details, or visit <http://unlicense.org>.

from typing import cast, Dict, Tuple, List, Type, Optional

import sys, copy, nbdkit

from greaseweazle.tools import util
from greaseweazle import error
from greaseweazle import usb as USB
from greaseweazle.flux import Flux, HasFlux
from greaseweazle.codec import codec
from greaseweazle.image import image

from greaseweazle import track
plls = track.plls

usb = None

class Track:
    pass

class Args:
    pass

args = Args()
args.drive = util.Drive()
args.device = None
args.gen_tg43 = False
args.revs = 1
args.ticks = 0
args.fake_index = None
args.drive_ticks_per_rev = None
args.reverse = False
args.hard_sectors = None
args.adjust_speed = None
args.sector_size = None
args.nsec = None

API_VERSION = 2
def config(key, value):
    match key:
        case 'format':
            args.fmt_cls = codec.get_diskdef(value, None)
        case 'drive':
            args.drive(value)
        case 'adjust_speed':
            args.adjust_speed = float(value)
        case 'gen_tg43':
            args.gen_tg43 = bool(value)
def config_complete():
    if args.fmt_cls is None:
        raise ValueError('disk format must be specified')
    for i, t in args.fmt_cls.track_map.items():
        if not args.nsec:
            args.nsec = t.secs
        if args.nsec != t.secs:
            raise ValueError('disk format must have same number of sectors on all tracks')
        for sz in t.sz:
            if not args.sector_size:
                args.sector_size = 2**(7+sz)
            if args.sector_size != 2**(7+sz):
                raise ValueError('disk format must have fixed sector size')
def thread_model():
    return nbdkit.THREAD_MODEL_SERIALIZE_CONNECTIONS
def can_cache(h):
    return nbdkit.CACHE_NONE
def is_rotational(h):
    return True
def open(readonly):
    global usb
    global args
    usb = util.usb_open(args.device)
def get_size(h):
    return args.fmt_cls.cyls*args.nsec*args.sector_size*args.fmt_cls.heads
def block_size(h):
    return (args.sector_size, args.sector_size, args.sector_size)
def pread(h, buf, offset, flags):
    global usb
    global args
    sz = args.sector_size
    sectors_per_track = args.nsec
    num_tracks = args.fmt_cls.cyls
    heads = args.fmt_cls.heads
    first_sector = offset // args.sector_size
    last_sector = (offset + len(buf) + sz - 1) // sz
    tracks = set()
    for sector_num in range(first_sector, last_sector):
        cyl = sector_num // (sectors_per_track * heads)
        head = (sector_num // sectors_per_track) % heads
        if (cyl < num_tracks) and (head < heads):
            tracks.add((cyl, head))
        
    def read_relevant_tracks():
        for tp in tracks:
            t = Track()
            t.cyl = tp[0]
            t.head = tp[1]
            # TODO: do correct mapping with TrackDef here
            t.physical_cyl = t.cyl
            t.physical_head = t.head
            flux, dat = read_with_retry(usb, args, t)
            bytes_track = dat.get_img_track()
            byte_offset_of_track = sz * sectors_per_track * (t.cyl * heads + t.head)
            for i, b in enumerate(bytes_track):
                if i + byte_offset_of_track >= offset and i + byte_offset_of_track < offset + len(buf):
                    buf[i + byte_offset_of_track - offset] = b
    util.with_drive_selected(read_relevant_tracks, usb, args.drive)

def read_and_normalise(usb: USB.Unit, args, revs: int, ticks=0) -> Flux:
    if args.fake_index is not None:
        drive_tpr = int(args.drive_ticks_per_rev)
        pre_index = int(usb.sample_freq * 0.5e-3)
        if ticks == 0:
            ticks = revs*drive_tpr + 2*pre_index
        flux = usb.read_track(revs=0, ticks=ticks)
        index_list = [pre_index] + [drive_tpr] * ((ticks-pre_index)//drive_tpr)
        flux.index_list = cast(List[float], index_list) # mypy
    else:
        flux = usb.read_track(revs=revs, ticks=ticks)
    flux._ticks_per_rev = args.drive_ticks_per_rev
    if args.reverse:
        flux.reverse()
    if args.hard_sectors and not args.raw:
        flux.identify_hard_sectors()
    if args.adjust_speed is not None:
        flux.scale(args.adjust_speed / flux.time_per_rev)
    return flux


def read_with_retry(usb: USB.Unit, args, t) -> Tuple[Flux, Optional[HasFlux]]:

    cyl, head = t.cyl, t.head

    tspec = f'T{cyl}.{head}'
    if t.physical_cyl != cyl or t.physical_head != head:
        tspec += f' <- Drive {t.physical_cyl}.{t.physical_head}'

    usb.seek(t.physical_cyl, t.physical_head)

    if args.gen_tg43:
        usb.set_pin(2, cyl < 60)

    flux = read_and_normalise(usb, args, args.revs, args.ticks)
    if args.fmt_cls is None:
        print(f'{tspec}: {flux.summary_string()}')
        return flux, flux

    dat = args.fmt_cls.decode_flux(cyl, head, flux)
    if dat is None:
        print("%s: WARNING: Out of range for format '%s': No format "
              "conversion applied: %s" % (tspec, args.format,
                flux.summary_string()))
        return flux, None
    for pll in plls[1:]:
        if dat.nr_missing() == 0:
            break
        dat.decode_flux(flux, pll)

    seek_retry, retry = 0, 0
    while True:
        s = "%s: %s from %s" % (tspec, dat.summary_string(),
                                    flux.summary_string())
        if retry != 0:
            s += " (Retry #%u.%u)" % (seek_retry, retry)
        print(s)
        if dat.nr_missing() == 0:
            break
        if args.retries == 0 or (retry % args.retries) == 0:
            if args.retries == 0 or seek_retry > args.seek_retries:
                print("%s: Giving up: %d sectors missing"
                      % (tspec, dat.nr_missing()))
                break
            if retry != 0:
                usb.seek(0, 0)
                usb.seek(t.physical_cyl, t.physical_head)
                if args.gen_tg43:
                    usb.set_pin(2, cyl < 60)
            seek_retry += 1
            retry = 0
        retry += 1
        _flux = read_and_normalise(usb, args, max(args.revs, 3))
        for pll in plls:
            if dat.nr_missing() == 0:
                break
            dat.decode_flux(_flux, pll)
        if args.raw:
            flux.append(_flux)
        else:
            flux = _flux

    return flux, dat

# Local variables:
# python-indent: 4
# End:
