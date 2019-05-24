# Copyright (C) 2012-2014 The python-bitcoinlib developers
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

"""Wallet-related functionality

Includes things like representing addresses and converting them to/from
scriptPubKeys; currently there is no actual wallet support implemented.
"""

# pylama:ignore=E501,E221

from abc import ABCMeta

import bitcointx
import bitcointx.base58
import bitcointx.bech32
import bitcointx.core
import bitcointx.core.key
from bitcointx.core.script import (
    CScript, CBitcoinScript, CScriptInvalidError,
    OP_HASH160, OP_DUP, OP_EQUALVERIFY, OP_CHECKSIG, OP_EQUAL
)


_wallet_class_params = {}  # to be filled by _SetWalletClassParams()


class _WalletClassParamsBase():
    def __new__(cls, *args, **kwargs):
        real_class = _wallet_class_params[cls]
        return real_class(*args, **kwargs)


class _WalletClassParamsMeta(ABCMeta):
    def __new__(cls, name, bases, dct):
        if cls not in _wallet_class_params:
            raise TypeError(
                'Concrete implementation for {} is not defined for current '
                'chain parameters'.format(cls.__name__))
        bases = [_WalletClassParamsBase] + list(bases)
        return super(
            _WalletClassParamsMeta, cls
        ).__new__(cls, name, tuple(bases), dct)

    def __getattr__(cls, name):
        if cls not in _wallet_class_params:
            raise TypeError(
                'Concrete implementation for {} is not defined for current '
                'chain parameters'.format(cls.__name__))
        real_class = _wallet_class_params[cls]
        return getattr(real_class, name)


class CCoinAddress(metaclass=_WalletClassParamsMeta):
    pass


class P2SHCoinAddress(CCoinAddress):
    pass


class P2PKHCoinAddress(CCoinAddress):
    pass


class P2WSHCoinAddress(CCoinAddress):
    pass


class P2WPKHCoinAddress(CCoinAddress):
    pass


class CCoinAddressCommon():

    _address_encoding_classes = None
    _script_class = None

    def __new__(cls, s):
        for enc_class in cls._address_encoding_classes:
            try:
                return enc_class(s)
            except bitcointx.core.AddressEncodingError:
                pass

        raise CCoinAddressError(
            'Unrecognized encoding for {}' .format(cls.__name__))

    @classmethod
    def set_class_params(cls, script_class=None, address_classes=()):
        if script_class is None and cls._script_class is not None:
            raise ValueError(
                '{} has no script class parameter set byits superclasses, '
                'therefore it should be specified when calling '
                'set_class_params'.format(cls.__name__))
        if not isinstance(script_class, CScript):
            raise ValueError(
                'script parameter should be a subclass of CScript')

        cls._script_class = script_class

        enc_class_list = []
        for enc_class, subclasses in address_classes:
            if enc_class._address_subclasses is not None:
                raise ValueError(
                    '{}._address_subclasses were already set earlier'
                    .format(enc_class.__name__))
            enc_class._address_subclasses = subclasses
            enc_class_list.append(enc_class)

        cls._address_encoding_classes = tuple(enc_class_list)

    @classmethod
    def from_scriptPubKey(cls, scriptPubKey):
        """Convert a scriptPubKey to a subclass of CCoinAddress"""
        for enc_class in cls._address_encoding_classes:
            try:
                return enc_class.from_scriptPubKey(scriptPubKey)
            except CCoinAddressError:
                pass

        raise CCoinAddressError(
            'scriptPubKey is not in a recognized address format')


class CConfidentialAddressBase():
    """Base class for all confidential addresses"""

    def to_scriptPubKey(self):
        return self.to_unconfidential().to_scriptPubKey()

    def to_redeemScript(self):
        return self.to_unconfidential().to_scriptPubKey()


class CCoinAddressError(Exception):
    """Raised when an invalid coin address is encountered"""


class CBase58AddressError(CCoinAddressError):
    """Raised when an invalid base58-encoded address is encountered
    (error is not necessary related to encoding)"""


class CBech32AddressError(CCoinAddressError):
    """Raised when an invalid bech32-encoded address is encountered
    (error is not necessary related to encoding)"""


class CConfidentialAddressError(CCoinAddressError):
    """Raised when an invalid confidential address is encountered"""


class P2SHCoinAddressError(CBase58AddressError):
    """Raised when an invalid P2SH address is encountered"""


class P2PKHCoinAddressError(CBase58AddressError):
    """Raised when an invalid P2PKH address is encountered"""


class P2WSHCoinAddressError(CBech32AddressError):
    """Raised when an invalid P2SH address is encountered"""


class P2WPKHCoinAddressError(CBech32AddressError):
    """Raised when an invalid P2PKH address is encountered"""


class CBech32CoinAddressCommon(bitcointx.bech32.CBech32Data):
    """A Bech32-encoded coin address"""

    _address_subclasses = None
    _data_length = None
    _witness_version = None

    @classmethod
    def from_bytes(cls, witprog, witver=None):

        if cls._witness_version is None:
            assert witver is not None, \
                ("witver must be specified for {}.from_bytes()"
                 .format(cls.__name__))
            for candidate in cls._address_subclasses:
                if len(witprog) == candidate._data_length and\
                        witver == candidate._witness_version:
                    break
            else:
                raise CBech32AddressError(
                    'witness program does not match any known Bech32 '
                    'address length')
        else:
            candidate = cls

        self = super(CBech32CoinAddressCommon, cls).from_bytes(
            bytes(witprog), witver=candidate._witness_version
        )
        self.__class__ = candidate

        return self

    @classmethod
    def from_scriptPubKey(cls, scriptPubKey):
        """Convert a scriptPubKey to subclass of CBech32CoinAddressCommon

        Returns a CBech32CoinAddressCommon subclass.
        If the scriptPubKey is not recognized CCoinAddressError will be raised.
        """
        for candidate in cls._address_subclasses:
            try:
                return candidate.from_scriptPubKey(scriptPubKey)
            except CCoinAddressError:
                pass

        raise CBech32AddressError(
            'scriptPubKey not a valid bech32-encoded address')


class CBase58CoinAddressCommon(bitcointx.base58.CBase58PrefixedData):
    """A Base58-encoded coin address"""

    base58_prefix = b''
    _data_length = None

    @classmethod
    def from_bytes_with_prefix(cls, data):
        if not cls.base58_prefix:
            return cls.match_base58_classes(data, cls._address_subclasses)
        return super(CBase58CoinAddressCommon, cls).from_bytes_with_prefix(data)

    @classmethod
    def from_bytes(cls, data):
        if not cls.base58_prefix:
            raise TypeError('from_bytes() method cannot be called on {}, '
                            'because base58_prefix is not defined for it'
                            .format(cls.__name__))
        return super(CBase58CoinAddressCommon, cls).from_bytes(data)

    @classmethod
    def from_scriptPubKey(cls, scriptPubKey):
        """Convert a scriptPubKey to a CCoinAddress

        Returns a CCoinAddress subclass:
            either subclass of P2SHCoinAddressCommon or
            subclass of P2PKHCoinAddressCommon.
            If the scriptPubKey is not recognized,
            CCoinAddressError will be raised.
        """
        for candidate in cls._address_subclasses:
            try:
                return candidate.from_scriptPubKey(scriptPubKey)
            except CCoinAddressError:
                pass

        raise CBase58AddressError(
            'scriptPubKey not a valid base58-encoded address')


class P2SHCoinAddressCommon():
    _data_length = 20

    @classmethod
    def from_redeemScript(cls, redeemScript):
        """Convert a redeemScript to a P2SH address

        Convenience function: equivalent to P2SHBitcoinAddress.from_scriptPubKey(redeemScript.to_p2sh_scriptPubKey())
        """
        return cls.from_scriptPubKey(redeemScript.to_p2sh_scriptPubKey())

    @classmethod
    def from_scriptPubKey(cls, scriptPubKey):
        """Convert a scriptPubKey to a P2SH address

        Raises CCoinAddressError if the scriptPubKey isn't of the correct
        form.
        """
        if scriptPubKey.is_p2sh():
            return cls.from_bytes(scriptPubKey[2:22])

        else:
            raise P2SHCoinAddressError('not a P2SH scriptPubKey')

    def to_scriptPubKey(self):
        """Convert an address to a scriptPubKey"""
        return self.__class__._script_class(
            [OP_HASH160, self, OP_EQUAL])

    def to_redeemScript(self):
        return self.to_scriptPubKey()


class P2PKHCoinAddressCommon():
    _data_length = 20

    @classmethod
    def from_pubkey(cls, pubkey, accept_invalid=False):
        """Create a P2PKH address from a pubkey

        Raises CCoinAddressError if pubkey is invalid, unless accept_invalid
        is True.

        The pubkey must be a bytes instance;
        """
        if not isinstance(pubkey, (bytes, bytearray)):
            raise TypeError('pubkey must be bytes or bytearray instance; got %r'
                            % pubkey.__class__)

        if not accept_invalid:
            if not isinstance(pubkey, bitcointx.core.key.CPubKey):
                pubkey = bitcointx.core.key.CPubKey(pubkey)
            if not pubkey.is_fullyvalid():
                raise P2PKHCoinAddressError('invalid pubkey')

        pubkey_hash = bitcointx.core.Hash160(pubkey)
        return cls.from_bytes(pubkey_hash)

    @classmethod
    def from_scriptPubKey(cls, scriptPubKey, accept_non_canonical_pushdata=True, accept_bare_checksig=True):
        """Convert a scriptPubKey to a P2PKH address

        Raises CCoinAddressError if the scriptPubKey isn't of the correct
        form.

        accept_non_canonical_pushdata - Allow non-canonical pushes (default True)

        accept_bare_checksig          - Treat bare-checksig as P2PKH scriptPubKeys (default True)
        """
        if accept_non_canonical_pushdata:
            # Canonicalize script pushes

            # in case it's not a CScript instance yet
            scriptPubKey = cls._script_class(scriptPubKey)

            try:
                # canonicalize
                scriptPubKey = cls._script_class(tuple(scriptPubKey))
            except CScriptInvalidError:
                raise P2PKHCoinAddressError(
                    'not a P2PKH scriptPubKey: script is invalid')

        if scriptPubKey.is_witness_v0_keyhash():
            return cls.from_bytes(scriptPubKey[2:22])
        elif scriptPubKey.is_witness_v0_nested_keyhash():
            return cls.from_bytes(scriptPubKey[3:23])
        elif (len(scriptPubKey) == 25
                and scriptPubKey[0]  == OP_DUP
                and scriptPubKey[1]  == OP_HASH160
                and scriptPubKey[2]  == 0x14
                and scriptPubKey[23] == OP_EQUALVERIFY
                and scriptPubKey[24] == OP_CHECKSIG):
            return cls.from_bytes(scriptPubKey[3:23])

        elif accept_bare_checksig:
            pubkey = None

            # We can operate on the raw bytes directly because we've
            # canonicalized everything above.
            if (len(scriptPubKey) == 35  # compressed
                    and scriptPubKey[0]  == 0x21
                    and scriptPubKey[34] == OP_CHECKSIG):

                pubkey = scriptPubKey[1:34]

            elif (len(scriptPubKey) == 67  # uncompressed
                    and scriptPubKey[0] == 0x41
                    and scriptPubKey[66] == OP_CHECKSIG):

                pubkey = scriptPubKey[1:66]

            if pubkey is not None:
                return cls.from_pubkey(pubkey, accept_invalid=True)

        raise P2PKHCoinAddressError('not a P2PKH scriptPubKey')

    def to_scriptPubKey(self):
        """Convert an address to a scriptPubKey"""
        return self.__class__._script_class(
            [OP_DUP, OP_HASH160, self,
             OP_EQUALVERIFY, OP_CHECKSIG])

    def to_redeemScript(self):
        return self.to_scriptPubKey()


class P2WSHCoinAddressCommon():
    _data_length = 32
    _witness_version = 0

    @classmethod
    def from_scriptPubKey(cls, scriptPubKey):
        """Convert a scriptPubKey to a P2WSH address

        Raises CCoinAddressError if the scriptPubKey isn't of the correct
        form.
        """
        if scriptPubKey.is_witness_v0_scripthash():
            return cls.from_bytes(scriptPubKey[2:34])
        else:
            raise P2WSHCoinAddressError('not a P2WSH scriptPubKey')

    @classmethod
    def from_redeemScript(cls, redeemScript):
        """Convert a redeemScript to a P2WSH address

        Convenience function: equivalent to
        P2WSHBitcoinAddress.from_scriptPubKey(redeemScript.to_p2wsh_scriptPubKey())
        """
        return cls.from_scriptPubKey(redeemScript.to_p2wsh_scriptPubKey())

    def to_scriptPubKey(self):
        """Convert an address to a scriptPubKey"""
        return self.__class__._script_class([0, self])

    def to_redeemScript(self):
        raise NotImplementedError(
            "not enough data in p2wsh address to reconstruct redeem script")


class P2WPKHCoinAddressCommon():
    _data_length = 20
    _witness_version = 0

    @classmethod
    def from_pubkey(cls, pubkey, accept_invalid=False):
        """Create a P2WPKH address from a pubkey

        Raises CCoinAddressError if pubkey is invalid, unless accept_invalid
        is True.

        The pubkey must be a bytes instance;
        """
        if not isinstance(pubkey, (bytes, bytearray)):
            raise TypeError('pubkey must be bytes or bytearray instance; got %r'
                            % pubkey.__class__)

        if not accept_invalid:
            if not isinstance(pubkey, bitcointx.core.key.CPubKey):
                pubkey = bitcointx.core.key.CPubKey(pubkey)
            if not pubkey.is_fullyvalid():
                raise P2PKHCoinAddressError('invalid pubkey')

        pubkey_hash = bitcointx.core.Hash160(pubkey)
        return cls.from_bytes(pubkey_hash)

    @classmethod
    def from_scriptPubKey(cls, scriptPubKey):
        """Convert a scriptPubKey to a P2WSH address

        Raises CCoinAddressError if the scriptPubKey isn't of the correct
        form.
        """
        if scriptPubKey.is_witness_v0_keyhash():
            return cls.from_bytes(scriptPubKey[2:22])
        else:
            raise P2WPKHCoinAddressError('not a P2WPKH scriptPubKey')

    def to_scriptPubKey(self):
        """Convert an address to a scriptPubKey"""
        return self.__class__._script_class([0, self])

    def to_redeemScript(self):
        return self.__class__._script_class(
            [OP_DUP, OP_HASH160, self,
             OP_EQUALVERIFY, OP_CHECKSIG])


class CBitcoinAddress(CCoinAddressCommon):
    ...


class CBitcoinTestnetAddress(CBitcoinAddress):
    ...


class CBase58BitcoinAddress(CBase58CoinAddressCommon, CBitcoinAddress):
    ...


class CBase58BitcoinTestnetAddress(CBase58CoinAddressCommon,
                                   CBitcoinTestnetAddress):
    ...


class CBech32BitcoinAddress(CBech32CoinAddressCommon, CBitcoinAddress):
    bech32_hrp = 'bc'


class CBech32BitcoinTestnetAddress(CBech32CoinAddressCommon,
                                   CBitcoinTestnetAddress):
    bech32_hrp = 'tb'


class CBech32BitcoinRegtestAddress(CBech32CoinAddressCommon,
                                   CBitcoinTestnetAddress):
    bech32_hrp = 'bcrt'


class P2SHBitcoinAddress(P2SHCoinAddressCommon, CBase58BitcoinAddress):
    base58_prefix = bytes([5])


class P2PKHBitcoinAddress(P2PKHCoinAddressCommon, CBase58BitcoinAddress):
    base58_prefix = bytes([0])


class P2PKHBitcoinTestnetAddress(P2PKHCoinAddressCommon,
                                 CBase58BitcoinTestnetAddress):
    base58_prefix = bytes([111])


class P2SHBitcoinTestnetAddress(P2SHCoinAddressCommon,
                                CBase58BitcoinTestnetAddress):
    base58_prefix = bytes([196])


class P2WSHBitcoinAddress(P2WSHCoinAddressCommon, CBech32BitcoinAddress):
    ...


class P2WPKHBitcoinAddress(P2WPKHCoinAddressCommon, CBech32BitcoinAddress):
    ...


class P2WSHBitcoinTestnetAddress(P2WSHCoinAddressCommon,
                                 CBech32BitcoinTestnetAddress):
    ...


class P2WPKHBitcoinTestnetAddress(P2WPKHCoinAddressCommon,
                                  CBech32BitcoinTestnetAddress):
    ...


class P2WSHBitcoinRegtestAddress(P2WSHCoinAddressCommon,
                                 CBech32BitcoinRegtestAddress):
    ...


class P2WPKHBitcoinRegtestAddress(P2WPKHCoinAddressCommon,
                                  CBech32BitcoinRegtestAddress):
    ...


# Make CBitcoinAddress behave like a a subclass of CCoinAddress
# regarding isinstance(script, CCoinAddress), etc
CCoinAddress.register(CBitcoinAddress)

CBitcoinAddress.set_class_params(
    script_class=CBitcoinScript,
    address_classes=(
        [CBech32BitcoinAddress,
         (P2WSHBitcoinAddress, P2WPKHBitcoinAddress)],
        [CBase58BitcoinAddress,
         (P2SHBitcoinAddress, P2PKHBitcoinAddress)]
    )
)

CBitcoinTestnetAddress.set_class_params(
    # CBitcoinTestnetAddress is a subclass of CBitcoinAddress,
    # this means script class is already registered, no specify here
    address_classes=(
        [CBech32BitcoinTestnetAddress,
         (P2WSHBitcoinTestnetAddress, P2WPKHBitcoinTestnetAddress)],
        [CBech32BitcoinRegtestAddress,
         (P2WSHBitcoinRegtestAddress, P2WPKHBitcoinRegtestAddress)],
        [CBase58BitcoinTestnetAddress,
         (P2SHBitcoinTestnetAddress, P2PKHBitcoinTestnetAddress)]
    )
)


class CCoinSecret(metaclass=_WalletClassParamsMeta):
    pass


class CCoinSecretBase(bitcointx.base58.CBase58PrefixedData, bitcointx.core.key.CKeyMixin):

    """A base58-encoded secret key

    Attributes: (inherited from CKeyMixin):

    pub           - The corresponding CPubKey for this private key
    secret_bytes  - Secret data, 32 bytes

    is_compressed() - True if compressed

    Note that CCoinSecretBase instance is 33 bytes long if compressed, 32 bytes otherwise
    (due to WIF format that states b'\x01' should be appended for compressed keys).
    secret_bytes property is 32 bytes long in both cases.

    """

    @classmethod
    def from_bytes(cls, data):
        if len(data) > 33:
            raise ValueError('data size must not exceed 33 bytes')
        compressed = (len(data) > 32 and data[32] == 1)
        self = super(CCoinSecretBase, cls).from_bytes(data)
        bitcointx.core.key.CKeyMixin.__init__(self, None, compressed=compressed)
        return self

    @classmethod
    def from_secret_bytes(cls, secret, compressed=True):
        """Create a secret key from a 32-byte secret"""
        if len(secret) != 32:
            raise ValueError('secret size must be exactly 32 bytes')
        self = super(CCoinSecretBase, cls).from_bytes(secret + (b'\x01' if compressed else b''))
        bitcointx.core.key.CKeyMixin.__init__(self, None, compressed=compressed)
        return self

    def to_compressed(self):
        if self.is_compressed():
            return self
        return self.__class__.from_secret_bytes(self[:32], True)

    def to_uncompressed(self):
        if not self.is_compressed():
            return self
        return self.__class__.from_secret_bytes(self[:32], False)


class CBitcoinSecret(CCoinSecretBase):
    base58_prefix = bytes([128])


class CBitcoinTestnetSecret(CBitcoinSecret):
    base58_prefix = bytes([239])


class CCoinExtSecret(metaclass=_WalletClassParamsMeta):
    pass


class CCoinExtSecretBase(bitcointx.base58.CBase58PrefixedData):
    """A base58-encoded extended key

    Attributes (inherited from key mixin class):

    pub           - The corresponding CPubKey for extended pubkey
    priv          - The corresponding CBitcoinSecret for extended privkey
                    (only present for extended private key class)
    """

    base58_prefix = b''

    @classmethod
    def from_bytes_with_prefix(cls, data):
        if not cls.base58_prefix:
            return cls.match_base58_classes(data, (cls._xpriv_class,
                                                   cls._xpub_class))
        return super(CCoinExtSecretBase, cls).from_bytes_with_prefix(data)

    @classmethod
    def from_bytes(cls, data):
        if not cls.base58_prefix:
            raise TypeError('from_bytes() method cannot be called on {}, '
                            'because base58_prefix is not defined for it'
                            .format(cls.__name__))
        return super(CCoinExtSecretBase, cls).from_bytes(data)

    def __init__(self, _s):
        assert isinstance(self, self.__class__._key_mixin_class)
        self.__class__._key_mixin_class.__init__(self, None)


class CCoinExtPubKeyCommon(bitcointx.core.key.CExtPubKeyMixin):
    _key_mixin_class = bitcointx.core.key.CExtPubKeyMixin


class CCoinExtKeyCommon(bitcointx.core.key.CExtKeyMixin):
    _key_mixin_class = bitcointx.core.key.CExtKeyMixin


class CBitcoinExtSecret(CCoinExtSecretBase):
    ...


class CBitcoinTestnetExtSecret(CBitcoinExtSecret):
    ...


class CBitcoinExtPubKey(CBitcoinExtSecret, CCoinExtPubKeyCommon):
    base58_prefix = b'\x04\x88\xB2\x1E'


class CBitcoinTestnetExtPubKey(CBitcoinTestnetExtSecret, CCoinExtPubKeyCommon):
    base58_prefix = b'\x04\x35\x87\xCF'


class CBitcoinExtKey(CBitcoinExtSecret, CCoinExtKeyCommon):
    base58_prefix = b'\x04\x88\xAD\xE4'


CBitcoinExtSecret._xpriv_class = CBitcoinExtKey
CBitcoinExtSecret._xpub_class = CBitcoinExtPubKey
CBitcoinExtSecret._key_class = CBitcoinSecret


class CBitcoinTestnetExtKey(CBitcoinTestnetExtSecret):
    base58_prefix = b'\x04\x35\x83\x94'


CBitcoinTestnetExtSecret._xpriv_class = CBitcoinTestnetExtKey
CBitcoinTestnetExtSecret._xpub_class = CBitcoinTestnetExtPubKey
CBitcoinTestnetExtSecret._key_class = CBitcoinTestnetSecret


def _SetAddressClassParams(address_cls, secret_cls, ext_secret_cls):
    _wallet_class_params[CCoinAddress] = address_cls
    _wallet_class_params[CCoinSecret] = secret_cls
    _wallet_class_params[CCoinExtSecret] = ext_secret_cls


_SetAddressClassParams(CBitcoinAddress, CBitcoinSecret, CBitcoinExtSecret)

__all__ = (
    'CCoinAddressError',
    'P2SHCoinAddressError',
    'P2PKHCoinAddressError',
    'P2WSHCoinAddressError',
    'P2WPKHCoinAddressError',
    'CCoinAddress',
    'CBitcoinAddress',
    'CBitcoinTestnetAddress',
    'CBase58CoinAddressCommon',
    'CBech32CoinAddressCommon',
    'P2SHCoinAddressCommon',
    'P2PKHCoinAddressCommon',
    'P2WSHCoinAddressCommon',
    'P2WPKHCoinAddressCommon',
    'CBase58BitcoinAddress',
    'CBech32BitcoinAddress',
    'P2SHBitcoinAddress',
    'P2PKHBitcoinAddress',
    'P2WSHBitcoinAddress',
    'P2WPKHBitcoinAddress',
    'CBase58BitcoinTestnetAddress',
    'CBech32BitcoinTestnetAddress',
    'P2SHBitcoinTestnetAddress',
    'P2PKHBitcoinTestnetAddress',
    'P2WSHBitcoinTestnetAddress',
    'P2WPKHBitcoinTestnetAddress',
    'CBitcoinSecret',
    'CBitcoinExtKey',
    'CBitcoinExtPubKey',
)
