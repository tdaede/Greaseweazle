# greaseweazle/circular.py
#
# Circular versions of lists and bitarrays.
#
# Written & released by Keir Fraser <keir.xen@gmail.com>
#
# This is free and unencumbered software released into the public domain.
# See the file COPYING for more details, or visit <http://unlicense.org>.

from bitarray import bitarray

def _setitem(self, parent, key, val):
    if not self.circular:
        return parent.__setitem__(key, val)
    if not isinstance(key, slice):
        return parent.__setitem__(key % len(self), val)
    s, e = key.start, key.stop
    if s is None: s = 0
    if e is None: e = len(self)
    assert key.step is None
    n = e - s
    assert n == len(val)
    s %= len(self)
    nr_to_end = len(self) - s
    if n <= nr_to_end:
        return parent.__setitem__(slice(s,s+n), val)
    parent.__setitem__(slice(s,s+nr_to_end), val[:nr_to_end])
    val = val[nr_to_end:]
    while val:
        nr_todo = min(len(val), len(self))
        parent.__setitem__(slice(0,nr_todo), val[:nr_todo])
        val = val[nr_todo:]

def _getitem(self, parent, key):
    if not self.circular:
        return parent.__getitem__(key)
    if not isinstance(key, slice):
        return parent.__getitem__(key % len(self))
    s, e = key.start, key.stop
    if s is None: s = 0
    if e is None: e = len(self)
    assert key.step is None
    n = e - s
    assert n >= 0
    s %= len(self)
    nr_to_end = len(self) - s
    if n <= nr_to_end:
        return parent.__getitem__(slice(s,s+n))
    x = parent.__getitem__(slice(s,s+nr_to_end))
    n -= nr_to_end
    while n > 0:
        nr_todo = min(n, len(self))
        x += parent.__getitem__(slice(0,nr_todo))
        n -= nr_todo
    return x


class circular_list(list):

    circular = True
    
    def __setitem__(self, key, val):
        _setitem(self, super(), key, val)

    def __getitem__(self, key):
        return _getitem(self, super(), key)


class circular_bitarray(bitarray):
    circular = True

    def __setitem__(self, key, val):
        _setitem(self, super(), key, val)

    def __getitem__(self, key):
        return _getitem(self, super(), key)


if __name__ == "__main__":

    # Initialise a circular bitarray by passing a basic bitarray
    x = bitarray([1,0,1])
    y = circular_bitarray(x)
    
    # Same for a list
    x = [1,2,3]
    y = circular_list(x)

    y[5] = 20
    print(y, len(y)) # [1, 2, 20] 3
    # x[5] = 20  <-- won't work!

    y[-1:1] = [5,6]
    print(y) # [6, 2, 5]
    y.circular = False
    y[-1:1] = [10,11]
    print(y) # [6, 2, 10, 11, 5] (!!!)

# Local variables:
# python-indent: 4
# End:
