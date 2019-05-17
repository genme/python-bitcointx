# Copyright (C) 2011 Sam Rushing
# Copyright (C) 2013-2014 The python-bitcoinlib developers
# Copyright (C) 2018-2019 The python-bitcointx developers
#
# This file is part of python-bitcointx.
#
# It is subject to the license terms in the LICENSE file found in the top-level
# directory of this distribution.
#
# No part of python-bitcointx, including this file, may be copied, modified,
# propagated, or distributed except according to the terms contained in the
# LICENSE file.

# pylama:ignore=E501

"""Base58 encoding and decoding"""

import binascii

import bitcointx.core

B58_DIGITS = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


class Base58Error(bitcointx.core.AddressEncodingError):
    pass


class UnexpectedBase58PrefixError(Base58Error):
    """Raised by check_base58_prefix_correct() when unexpected prefix encountered

    """
    pass


class InvalidBase58Error(Base58Error):
    """Raised on generic invalid base58 data, such as bad characters.

    Checksum failures raise Base58ChecksumError specifically.
    """
    pass


def encode(b):
    """Encode bytes to a base58-encoded string"""

    # Convert big-endian bytes to integer
    n = int('0x0' + binascii.hexlify(b).decode('utf8'), 16)

    # Divide that integer into bas58
    res = []
    while n > 0:
        n, r = divmod(n, 58)
        res.append(B58_DIGITS[r])
    res = ''.join(res[::-1])

    # Encode leading zeros as base58 zeros
    czero = 0
    pad = 0
    for c in b:
        if c == czero:
            pad += 1
        else:
            break
    return B58_DIGITS[0] * pad + res


def decode(s):
    """Decode a base58-encoding string, returning bytes"""
    if not s:
        return b''

    # Convert the string to an integer
    n = 0
    for c in s:
        n *= 58
        if c not in B58_DIGITS:
            raise InvalidBase58Error('Character %r is not a valid base58 character' % c)
        digit = B58_DIGITS.index(c)
        n += digit

    # Convert the integer to bytes
    h = '%x' % n
    if len(h) % 2:
        h = '0' + h
    res = binascii.unhexlify(h.encode('utf8'))

    # Add padding back.
    pad = 0
    for c in s[:-1]:
        if c == B58_DIGITS[0]:
            pad += 1
        else:
            break
    return b'\x00' * pad + res


class Base58ChecksumError(Base58Error):
    """Raised on Base58 checksum errors"""
    pass


class CBase58RawData(bytes):
    """Base58-encoded data

    Includes prefix and checksum.
    """

    def __new__(cls, s):
        k = decode(s)
        if len(k) < 4:
            raise Base58Error('data too short')
        data, check0 = k[0:-4], k[-4:]
        check1 = bitcointx.core.Hash(data)[:4]
        if check0 != check1:
            raise Base58ChecksumError('Checksum mismatch: expected %r, calculated %r' % (check0, check1))
        return cls.from_bytes_with_prefix(data)

    def __init__(self, s):
        """Initialize from base58-encoded string

        Note: subclasses put your initialization routines here, but ignore the
        argument - that's handled by __new__(), and .from_bytes() will call
        __init__() with None in place of the string.
        """

    @classmethod
    def from_bytes_with_prefix(cls, data):
        """Instantiate from data with prefix.
        prefix is empty, so it is the same as from_bytes()"""
        return cls.from_bytes(cls, data)

    @classmethod
    def from_bytes(cls, data):
        """Instantiate from data"""
        self = bytes.__new__(cls, data)
        self.__init__(None)
        return self

    def to_bytes(self):
        """Convert to bytes instance

        Note that it's the data represented that is converted;
        the prefix is not included.
        """
        return b'' + self

    def __str__(self):
        """Convert to string"""
        check = bitcointx.core.Hash(self)[0:4]
        return encode(self + check)

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, str(self))


class CBase58PrefixedData(CBase58RawData):

    def __str__(self):
        """Convert to string"""
        check = bitcointx.core.Hash(self.base58_prefix + self)[0:4]
        return encode(self.base58_prefix + self + check)

    @classmethod
    def from_bytes_with_prefix(cls, data):
        """Instantiate from data with prefix."""
        pfx_len = len(cls.base58_prefix)
        prefix, data = data[:pfx_len], data[pfx_len:]
        if prefix != cls.base58_prefix:
            raise UnexpectedBase58PrefixError(
                'Incorrect prefix bytes for {}: {}, expected {}'
                .format(cls.__name__,
                        bitcointx.core.b2x(prefix),
                        bitcointx.core.b2x(cls.base58_prefix)))
        return cls.from_bytes(data)

    @classmethod
    def match_base58_classes(cls, data, candidates):
        for candidate in candidates:
            pfx = candidate.base58_prefix
            if data[:len(pfx)] == pfx:
                return candidate.from_bytes(data[len(pfx):])
        raise UnexpectedBase58PrefixError("Base58 prefix for {} not recognized")


__all__ = (
        'B58_DIGITS',
        'Base58Error',
        'InvalidBase58Error',
        'encode',
        'decode',
        'Base58ChecksumError',
        'CBase58RawData',
        'CBase58PrefixedData',
)
