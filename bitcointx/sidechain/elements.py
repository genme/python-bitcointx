# Copyright (C) 2018 The python-bitcointx developers
# Copyright (C) 2012-2017 The python-bitcoinlib developers
#
# This file is part of python-bitcointx.
#
# It is subject to the license terms in the LICENSE file found in the top-level
# directory of this distribution.
#
# No part of python-bitcointx, including this file, may be copied, modified,
# propagated, or distributed except according to the terms contained in the
# LICENSE file.
#
# Some code in this file is a direct translation from C++ code
# from Elements Project (https://github.com/ElementsProject/elements)
# Original C++ code was Copyright (c) 2017-2018 The Elements Core developers
# Original C++ code was under MIT license.

# pylama:ignore=E501

import os
import hmac
import struct
import ctypes
import hashlib
from collections import namedtuple

from bitcointx.core.secp256k1 import (
    secp256k1, secp256k1_has_zkp,
    secp256k1_blind_context,
    SECP256K1_GENERATOR_SIZE, SECP256K1_PEDERSEN_COMMITMENT_SIZE,
    build_aligned_data_array
)

from bitcointx.core import (
    CoreMainParams, Uint256, MoneyRange,
    bytes_for_repr, ReprOrStrMixin, b2x,
    CTxWitnessBase, CTxInWitnessBase, CTxOutWitnessBase,
    CTxInBase, CTxOutBase, COutPoint, CMutableOutPoint,
    CImmutableTransactionBase, CMutableTransactionBase
)
from bitcointx.core.key import CKey, CKeyMixin, CPubKey
from bitcointx.core.script import CScript, CScriptBase, CScriptWitness
from bitcointx.core.sha256 import CSHA256
from bitcointx.core.serialize import (
    ImmutableSerializable, SerializationError,
    BytesSerializer, VectorSerializer,
    ser_read, make_mutable
)

# If this flag is set, the CTxIn including this COutPoint has a CAssetIssuance object.
OUTPOINT_ISSUANCE_FLAG = (1 << 31)
# If this flag is set, the CTxIn including this COutPoint is a peg-in input.
OUTPOINT_PEGIN_FLAG = (1 << 30)
# The inverse of the combination of the preceeding flags. Used to
# extract the original meaning of `n` as the index into the
# transaction's output array. */
OUTPOINT_INDEX_MASK = 0x3fffffff


class WitnessSerializationError(SerializationError):
    pass


class TxInSerializationError(SerializationError):
    pass


class CConfidentialCommitmentBase(ImmutableSerializable):
    _explicitSize = None
    _prefixA = None
    _prefixB = None

    _committedSize = 33

    __slots__ = ['commitment']

    def __init__(self, commitment=b''):
        object.__setattr__(self, 'commitment', commitment)

    @classmethod
    def stream_deserialize(cls, f):
        version = ser_read(f, 1)[0]
        read_size = 0
        if version == 0:
            read_size = 0
        elif version == 1:
            read_size = cls._explicitSize
        elif version in (cls._prefixA, cls._prefixB):
            read_size = cls._committedSize
        else:
            raise WitnessSerializationError('Unrecognized serialization prefix')

        if read_size > 0:
            commitment = bytes([version]) + ser_read(f, read_size-1)
        else:
            commitment = b''

        return cls(commitment)

    def stream_serialize(self, f):
        if len(self.commitment):
            f.write(self.commitment)
        else:
            f.write(bytes([0]))

    def is_null(self):
        return not len(self.commitment)

    def is_explicit(self):
        return (len(self.commitment) == self._explicitSize
                and self.commitment[0] == 1)

    def is_commitment(self):
        return (len(self.commitment) == self._committedSize
                and self.commitment[0] in (self._prefixA, self._prefixB))

    def is_valid(self):
        return self.is_null() or self.is_explicit() or self.is_commitment()

    def _get_explicit(self):
        raise NotImplementedError

    def __str__(self):
        if self.is_explicit():
            v = str(self._get_explicit())
        else:
            v = 'CONFIDENTIAL'
        return "{}({})".format(self.__class__.__name__, v)

    def __repr__(self):
        if self.is_explicit():
            v = repr(self._get_explicit())
        else:
            v = bytes_for_repr(self.commitment)
        return "{}({})".format(self.__class__.__name__, v)


class CAsset():
    __slots__ = ['id']

    def __init__(self, id=Uint256()):
        if not isinstance(id, Uint256):
            id = Uint256(id)
        object.__setattr__(self, 'id', id)

    def __repr__(self):
        return "CAsset({})".format(self.id)


class CConfidentialAsset(CConfidentialCommitmentBase):
    _explicitSize = 33
    _prefixA = 10
    _prefixB = 11

    def __init__(self, asset_or_commitment=b''):
        assert(isinstance(asset_or_commitment, (CAsset, bytes)))
        if isinstance(asset_or_commitment, CAsset):
            commitment = bytes([1]) + asset_or_commitment
        else:
            commitment = asset_or_commitment
        super(CConfidentialAsset, self).__init__(commitment)

    @classmethod
    def from_asset(cls, asset):
        assert isinstance(asset, CAsset)
        return cls(asset)

    def to_asset(self):
        assert self.is_explicit()
        return CAsset(self.commitment[1:])

    def _get_explicit(self):
        return self.to_asset()


class CConfidentialValue(CConfidentialCommitmentBase):
    _explicitSize = 9
    _prefixA = 8
    _prefixB = 9

    def __init__(self, value_or_commitment=b''):
        assert isinstance(value_or_commitment, (int, bytes))
        if isinstance(value_or_commitment, int):
            commitment = bytes([1]) + struct.pack(b">q", value_or_commitment)
        else:
            commitment = value_or_commitment
        super(CConfidentialValue, self).__init__(commitment)

    @classmethod
    def from_amount(cls, amount):
        assert isinstance(amount, int)
        return cls(amount)

    def to_amount(self):
        assert self.is_explicit()
        return struct.unpack(b">q", self.commitment[1:])[0]

    def _get_explicit(self):
        return self.to_amount()


class CConfidentialNonce(CConfidentialCommitmentBase):
    _explicitSize = 33
    _prefixA = 2
    _prefixB = 3

    def _get_explicit(self):
        return 'CONFIDENTIAL'

    def __repr__(self):
        v = "x('{}')".format(b2x(self.commitment))
        return "{}({})".format(self.__class__.__name__, v)


class CElementsSidechainTxInWitness(CTxInWitnessBase, ReprOrStrMixin):
    """Witness data for a single transaction input of elements sidechain transaction"""
    __slots__ = ['scriptWitness',
                 'issuanceAmountRangeproof', 'inflationKeysRangeproof', 'pegin_witness']

    # put scriptWitness first for CTxInWitness(script_witness) to work
    # the same as with CBitcoinTxInWitness.
    def __init__(self, scriptWitness=CScriptWitness(),
                 issuanceAmountRangeproof=b'', inflationKeysRangeproof=b'',
                 pegin_witness=CScriptWitness()):
        assert isinstance(issuanceAmountRangeproof, bytes)
        assert isinstance(inflationKeysRangeproof, bytes)
        object.__setattr__(self, 'scriptWitness', scriptWitness)
        object.__setattr__(self, 'issuanceAmountRangeproof', CScript(issuanceAmountRangeproof))
        object.__setattr__(self, 'inflationKeysRangeproof', CScript(inflationKeysRangeproof))
        # Note that scriptWitness/pegin_witness naming convention mismatch
        # exists in reference client code, and is retained here.
        object.__setattr__(self, 'pegin_witness', pegin_witness)

    def is_null(self):
        return (not len(self.issuanceAmountRangeproof)
                and not len(self.inflationKeysRangeproof)
                and self.scriptWitness.is_null()
                and self.pegin_witness.is_null())

    @classmethod
    def stream_deserialize(cls, f):
        issuanceAmountRangeproof = CScript(BytesSerializer.stream_deserialize(f))
        inflationKeysRangeproof = CScript(BytesSerializer.stream_deserialize(f))
        scriptWitness = CScriptWitness.stream_deserialize(f)
        pegin_witness = CScriptWitness.stream_deserialize(f)
        return cls(scriptWitness, issuanceAmountRangeproof, inflationKeysRangeproof,
                   pegin_witness)

    def stream_serialize(self, f):
        BytesSerializer.stream_serialize(self.issuanceAmountRangeproof, f)
        BytesSerializer.stream_serialize(self.inflationKeysRangeproof, f)
        self.scriptWitness.stream_serialize(f)
        self.pegin_witness.stream_serialize(f)

    @classmethod
    def from_txin_witness(cls, txin_witness):
        if not txin_witness._immutable_restriction_lifted:
            # txin_witness is immutable, therefore returning same txin_witness is OK
            return txin_witness
        return cls(scriptWitness=txin_witness.scriptWitness,
                   issuanceAmountRangeproof=txin_witness.issuanceAmountRangeproof,
                   inflationKeysRangeproof=txin_witness.inflationKeysRangeproof,
                   pegin_witness=txin_witness.pegin_witness)

    def _repr_or_str(self, strfn):
        mutstr = 'Mutable' if self._immutable_restriction_lifted else ''
        if self.is_null():
            return "C{}TxInWitness()".format(mutstr)
        return "C{}TxInWitness({}, {}, {}, {})".format(
            mutstr,
            strfn(self.scriptWitness), bytes_for_repr(self.issuanceAmountRangeproof),
            bytes_for_repr(self.inflationKeysRangeproof), strfn(self.pegin_witness))


@make_mutable
class CElementsSidechainMutableTxInWitness(CElementsSidechainTxInWitness):
    @classmethod
    def from_txin_witness(cls, txin_witness):
        """Create a mutable copy of an existing COutPoint"""
        return cls(scriptWitness=txin_witness.scriptWitness,
                   issuanceAmountRangeproof=txin_witness.issuanceAmountRangeproof,
                   inflationKeysRangeproof=txin_witness.inflationKeysRangeproof,
                   pegin_witness=txin_witness.pegin_witness)


class CElementsSidechainTxOutWitness(CTxOutWitnessBase):
    """Witness data for a single transaction output of elements sidechain transaction"""
    __slots__ = ['surjectionproof', 'rangeproof']

    def __init__(self, surjectionproof=b'', rangeproof=b''):
        assert isinstance(surjectionproof, bytes)
        assert isinstance(rangeproof, bytes)
        object.__setattr__(self, 'surjectionproof', CScript(surjectionproof))
        object.__setattr__(self, 'rangeproof', CScript(rangeproof))

    def is_null(self):
        return not len(self.surjectionproof) and not len(self.rangeproof)

    @classmethod
    def stream_deserialize(cls, f):
        surjectionproof = CScript(BytesSerializer.stream_deserialize(f))
        rangeproof = CScript(BytesSerializer.stream_deserialize(f))
        return cls(surjectionproof, rangeproof)

    def stream_serialize(self, f):
        BytesSerializer.stream_serialize(self.surjectionproof, f)
        BytesSerializer.stream_serialize(self.rangeproof, f)

    def get_rangeproof_info(self):
        if not secp256k1_has_zkp:
            raise RuntimeError('secp256k1-zkp library is not available. '
                               ' get_rangeproof_info is not functional.')

        exp = ctypes.c_int()
        mantissa = ctypes.c_int()
        value_min = ctypes.c_uint64()
        value_max = ctypes.c_uint64()
        result = secp256k1.secp256k1_rangeproof_info(
            secp256k1_blind_context,
            ctypes.byref(exp), ctypes.byref(mantissa),
            ctypes.byref(value_min), ctypes.byref(value_max),
            self.rangeproof, len(self.rangeproof)
        )
        if result != 1:
            assert result == 0
            return None

        return ZKPRangeproofInfo(exp=exp.value, mantissa=mantissa.value,
                                 value_min=value_min.value, value_max=value_max.value)

    @classmethod
    def from_txout_witness(cls, txout_witness):
        if not txout_witness._immutable_restriction_lifted:
            # txout_witness is immutable, therefore returning same txout_witness is OK
            return txout_witness
        return cls(surjectionproof=txout_witness.surjectionproof,
                   rangeproof=txout_witness.rangeproof)

    def __repr__(self):
        mutstr = 'Mutable' if self._immutable_restriction_lifted else ''
        if self.is_null():
            return "C{}TxOutWitness()".format(mutstr)
        return "C{}TxOutWitness({}, {})".format(
            mutstr,
            bytes_for_repr(self.surjectionproof),
            bytes_for_repr(self.rangeproof))


@make_mutable
class CElementsSidechainMutableTxOutWitness(CElementsSidechainTxOutWitness):

    @classmethod
    def from_txout_witness(cls, txout_witness):
        return cls(surjectionproof=txout_witness.surjectionproof,
                   rangeproof=txout_witness.rangeproof)


class CElementsSidechainTxWitness(CTxWitnessBase, ReprOrStrMixin):
    _txin_witness_class = CElementsSidechainTxInWitness
    _txout_witness_class = CElementsSidechainTxOutWitness

    __slots__ = ['vtxinwit', 'vtxoutwit']

    def __init__(self, vtxinwit=(), vtxoutwit=()):
        object.__setattr__(self, 'vtxinwit', tuple(vtxinwit))
        object.__setattr__(self, 'vtxoutwit', tuple(vtxoutwit))

    def is_null(self):
        for n in range(len(self.vtxinwit)):
            if not self.vtxinwit[n].is_null():
                return False
        for n in range(len(self.vtxoutwit)):
            if not self.vtxoutwit[n].is_null():
                return False
        return True

    # NOTE: this cannot be a @classmethod like the others because we need to
    # know how many items to deserialize, which comes from len(vin)
    def stream_deserialize(self, f):
        vtxinwit = tuple(self._txin_witness_class.stream_deserialize(f) for dummy in
                         range(len(self.vtxinwit)))
        vtxoutwit = tuple(self._txout_witness_class.stream_deserialize(f) for dummy in
                          range(len(self.vtxoutwit)))
        return self.__class__(vtxinwit, vtxoutwit)

    def stream_serialize(self, f):
        for i in range(len(self.vtxinwit)):
            self.vtxinwit[i].stream_serialize(f)
        for i in range(len(self.vtxoutwit)):
            self.vtxoutwit[i].stream_serialize(f)

    @classmethod
    def from_witness(cls, witness):
        if not cls._immutable_restriction_lifted:
            return witness
        return cls(witness.vtxinwit, witness.vtxoutwit)

    def _repr_or_str(self, strfn):
        return "C%sTxWitness([%s], [%s])" % (
            'Mutable' if self._immutable_restriction_lifted else '',
            ','.join(strfn(w) for w in self.vtxinwit),
            ','.join(strfn(w) for w in self.vtxoutwit))


@make_mutable
class CElementsSidechainMutableTxWitness(CElementsSidechainTxWitness):
    __slots__ = []

    _txin_witness_class = CElementsSidechainMutableTxInWitness
    _txout_witness_class = CElementsSidechainMutableTxOutWitness

    def __init__(self, vtxinwit=(), vtxoutwit=()):
        self.vtxinwit = list(vtxinwit)
        self.vtxoutwit = list(vtxoutwit)

    @classmethod
    def from_witness(cls, witness):
        return cls(witness.vtxinwit, witness.vtxoutwit)


class CAssetIssuance(ImmutableSerializable, ReprOrStrMixin):
    __slots__ = ['assetBlindingNonce', 'assetEntropy', 'nAmount', 'nInflationKeys']

    def __init__(self, assetBlindingNonce=Uint256(), assetEntropy=Uint256(),
                 nAmount=CConfidentialValue(), nInflationKeys=CConfidentialValue()):
        object.__setattr__(self, 'assetBlindingNonce', assetBlindingNonce)
        object.__setattr__(self, 'assetEntropy', assetEntropy)
        object.__setattr__(self, 'nAmount', nAmount)
        object.__setattr__(self, 'nInflationKeys', nInflationKeys)

    def is_null(self):
        return self.nAmount.is_null() and self.nInflationKeys.is_null()

    @classmethod
    def stream_deserialize(cls, f):
        assetBlindingNonce = Uint256.stream_deserialize(f)
        assetEntropy = Uint256.stream_deserialize(f)
        nAmount = CConfidentialValue.stream_deserialize(f)
        nInflationKeys = CConfidentialValue.stream_deserialize(f)
        return cls(assetBlindingNonce, assetEntropy, nAmount, nInflationKeys)

    def stream_serialize(self, f):
        self.assetBlindingNonce.stream_serialize(f)
        self.assetEntropy.stream_serialize(f)
        self.nAmount.stream_serialize(f)
        self.nInflationKeys.stream_serialize(f)

    def _repr_or_str(self, strfn):
        r = []
        if self.assetBlindingNonce.to_int():
            r.append(bytes_for_repr(self.assetBlindingNonce.data))
        if self.assetEntropy.to_int():
            r.append(bytes_for_repr(self.assetEntropy.data))
        if not self.nAmount.is_null():
            r.append(strfn(self.nAmount))
        if not self.nInflationKeys.is_null():
            r.append(strfn(self.nInflationKeys))
        return 'CAssetIssuance({})'.format(', '.join(r))


class CElementsSidechainTxIn(CTxInBase, ReprOrStrMixin):
    """An input of an Elements sidechain transaction
    """
    __slots__ = ['prevout', 'scriptSig', 'nSequence', 'assetIssuance', 'is_pegin']

    def __init__(self, prevout=COutPoint(), scriptSig=CScript(), nSequence=0xffffffff,
                 assetIssuance=CAssetIssuance(), is_pegin=False):
        super(CElementsSidechainTxIn, self).__init__(prevout, scriptSig, nSequence)
        object.__setattr__(self, 'assetIssuance', assetIssuance)
        object.__setattr__(self, 'is_pegin', is_pegin)

    @classmethod
    def stream_deserialize(cls, f):
        base = CTxInBase.stream_deserialize(f)
        if base.prevout.n == 0xffffffff:
            # No asset issuance for Coinbase inputs.
            has_asset_issuance = False
            is_pegin = False
            prevout = base.prevout
        else:
            # The presence of the asset issuance object is indicated by
            # a bit set in the outpoint index field.
            has_asset_issuance = bool(base.prevout.n & OUTPOINT_ISSUANCE_FLAG)
            # The interpretation of this input as a peg-in is indicated by
            # a bit set in the outpoint index field.
            is_pegin = bool(base.prevout.n & OUTPOINT_PEGIN_FLAG)
            # The mode, if set, must be masked out of the outpoint so
            # that the in-memory index field retains its traditional
            # meaning of identifying the index into the output array
            # of the previous transaction.
            prevout = COutPoint(base.prevout.hash,
                                base.prevout.n & OUTPOINT_INDEX_MASK)

        if has_asset_issuance:
            assetIssuance = CAssetIssuance.stream_deserialize(f)
        else:
            assetIssuance = CAssetIssuance()

        return cls(prevout, base.scriptSig, base.nSequence, assetIssuance, is_pegin)

    def stream_serialize(self, f):
        if self.prevout.n == 0xffffffff:
            has_asset_issuance = False
            outpoint = self.prevout
        else:
            if self.prevout.n & ~OUTPOINT_INDEX_MASK:
                raise TxInSerializationError('High bits of prevout.n should not be set')

            has_asset_issuance = not self.assetIssuance.is_null()
            n = self.prevout.n & OUTPOINT_INDEX_MASK
            if has_asset_issuance:
                n |= OUTPOINT_ISSUANCE_FLAG
            if self.is_pegin:
                n |= OUTPOINT_PEGIN_FLAG
            outpoint = COutPoint(self.prevout.hash, n)

        COutPoint.stream_serialize(outpoint, f)
        BytesSerializer.stream_serialize(self.scriptSig, f)
        f.write(struct.pack(b"<I", self.nSequence))

        if has_asset_issuance:
            self.assetIssuance.stream_serialize(f)

    @classmethod
    def from_txin(cls, txin):
        """Create an immutable copy of an existing TxIn

        If txin is already immutable, it is returned directly.
        """
        if not txin._immutable_restriction_lifted:
            # txin is immutable, therefore returning same txin is OK
            return txin
        else:
            return cls(COutPoint.from_outpoint(txin.prevout), txin.scriptSig, txin.nSequence,
                       txin.assetIssuance)

    def _repr_or_str(self, strfn):
        return "C%sTxIn(%s, %s, 0x%x, %s, %r)" % (
            'Mutable' if self._immutable_restriction_lifted else '',
            strfn(self.prevout), repr(self.scriptSig),
            self.nSequence, strfn(self.assetIssuance),
            self.is_pegin)


@make_mutable
class CElementsSidechainMutableTxIn(CElementsSidechainTxIn):
    """A mutable Elements sidechain CTxIn"""
    __slots__ = []

    def __init__(self, prevout=None, scriptSig=CScript(), nSequence=0xffffffff,
                 assetIssuance=CAssetIssuance(), is_pegin=False):
        if prevout is None:
            prevout = CMutableOutPoint()
        super(CElementsSidechainMutableTxIn, self).__init__(prevout, scriptSig, nSequence,
                                                            assetIssuance, is_pegin)

    @classmethod
    def from_txin(cls, txin):
        """Create a fully mutable copy of an existing Elements sidechain TxIn"""
        prevout = CMutableOutPoint.from_outpoint(txin.prevout)
        return cls(prevout, txin.scriptSig, txin.nSequence, txin.assetIssuance)


class CElementsSidechainTxOut(CTxOutBase, ReprOrStrMixin):
    """An output of an Elements sidechain transaction
    """
    __slots__ = ['nValue', 'scriptPubKey', 'nAsset', 'nNonce']

    # nValue and scriptPubKey is first to be compatible with
    # CTxOut(nValue, scriptPubKey) calls
    def __init__(self, nValue=CConfidentialValue(), scriptPubKey=CScript(),
                 nAsset=CConfidentialAsset(), nNonce=CConfidentialNonce()):
        assert isinstance(nValue, CConfidentialValue)
        assert isinstance(nAsset, CConfidentialAsset)
        assert isinstance(nNonce, CConfidentialNonce)
        object.__setattr__(self, 'nAsset', nAsset)
        object.__setattr__(self, 'nValue', nValue)
        object.__setattr__(self, 'nNonce', nNonce)
        object.__setattr__(self, 'scriptPubKey', scriptPubKey)

    @classmethod
    def stream_deserialize(cls, f):
        nAsset = CConfidentialAsset.stream_deserialize(f)
        nValue = CConfidentialValue.stream_deserialize(f)
        nNonce = CConfidentialNonce.stream_deserialize(f)
        scriptPubKey = CScript(BytesSerializer.stream_deserialize(f))
        return cls(nValue, scriptPubKey, nAsset, nNonce)

    def stream_serialize(self, f):
        self.nAsset.stream_serialize(f)
        self.nValue.stream_serialize(f)
        self.nNonce.stream_serialize(f)
        BytesSerializer.stream_serialize(self.scriptPubKey, f)

    def is_null(self):
        return (self.nAsset.is_null() and self.nValue.is_null()
                and self.nNonce.is_null() and not len(self.scriptPubKey))

    def is_fee(self):
        return (not len(self.scriptPubKey)
                and self.nValue.is_explicit()
                and self.nAsset.is_explicit())

    def _repr_or_str(self, strfn):
        return "C{}TxOut({}, {}, {})".format(
            'Mutable' if self._immutable_restriction_lifted else '',
            strfn(self.nValue), repr(self.scriptPubKey), strfn(self.nAsset))

    @classmethod
    def from_txout(cls, txout):
        """Create an immutable copy of an existing Elements sidechain TxOut

        If txout is already immutable, then it will be returned directly.
        """
        if not txout._immutable_restriction_lifted:
            return txout
        else:
            return cls(txout.nValue, txout.scriptPubKey,
                       txout.nAsset, txout.nNonce)


@make_mutable
class CElementsSidechainMutableTxOut(CElementsSidechainTxOut):
    __slots__ = []

    @classmethod
    def from_txout(cls, txout):
        """Create a fullly mutable copy of an existing Elements sidechain TxOut"""
        return cls(txout.nValue, txout.scriptPubKey,
                   txout.nAsset, txout.nNonce)


class CElementsSidechainTransactionCommon():

    @classmethod
    def stream_deserialize(cls, f):
        """Deserialize transaction

        This implementation corresponds to Elements's SerializeTransaction() and
        consensus behavior. Note that Elements's DecodeHexTx() also has the
        option to attempt deserializing as a non-witness transaction first,
        falling back to the consensus behavior if it fails. The difference lies
        in transactions which have zero inputs: they are invalid but may be
        (de)serialized anyway for the purpose of signing them and adding
        inputs. If the behavior of DecodeHexTx() is needed it could be added,
        but not here.
        """
        # FIXME can't assume f is seekable
        nVersion = struct.unpack(b"<i", ser_read(f, 4))[0]

        markerbyte = 0
        flagbyte = struct.unpack(b'B', ser_read(f, 1))[0]
        if markerbyte == 0 and flagbyte == 1:
            vin = VectorSerializer.stream_deserialize(cls._txin_class, f)
            vout = VectorSerializer.stream_deserialize(cls._txout_class, f)
            wit = cls._witness_class(tuple(0 for dummy in range(len(vin))),
                                     tuple(0 for dummy in range(len(vout))))
            # Note: nLockTime goes before witness in Elements sidechain transactions
            nLockTime = struct.unpack(b"<I", ser_read(f, 4))[0]
            wit = wit.stream_deserialize(f)
            return cls(vin, vout, nLockTime, nVersion, wit)
        else:
            vin = VectorSerializer.stream_deserialize(cls._txin_class, f)
            vout = VectorSerializer.stream_deserialize(cls._txout_class, f)
            nLockTime = struct.unpack(b"<I", ser_read(f, 4))[0]
            return cls(vin, vout, nLockTime, nVersion)

    def stream_serialize(self, f, include_witness=True):
        f.write(struct.pack(b"<i", self.nVersion))
        if include_witness and not self.wit.is_null():
            assert(len(self.wit.vtxinwit) == len(self.vin))
            f.write(b'\x01')  # Flag
            VectorSerializer.stream_serialize(self._txin_class, self.vin, f)
            VectorSerializer.stream_serialize(self._txout_class, self.vout, f)
            # Note: nLockTime goes before witness in Elements sidechain transactions
            f.write(struct.pack(b"<I", self.nLockTime))
            self.wit.stream_serialize(f)
        else:
            f.write(b'\x00')  # Flag is needed in Elements sidechain
            VectorSerializer.stream_serialize(self._txin_class, self.vin, f)
            VectorSerializer.stream_serialize(self._txout_class, self.vout, f)
            f.write(struct.pack(b"<I", self.nLockTime))

    @property
    def num_issuances(self):
        numIssuances = 0
        for txin in self.vin:
            if not txin.assetIssuance.is_null():
                if not txin.assetIssuance.nAmount.is_null():
                    numIssuances += 1
                if not txin.assetIssuance.nInflationKeys.is_null():
                    numIssuances += 1

        return numIssuances


class CElementsSidechainMutableTransaction(CElementsSidechainTransactionCommon, CMutableTransactionBase):
    # _inverted_mutability_class will be set in _SelectAlternativeCoreParams
    _witness_class = CElementsSidechainMutableTxWitness
    _txin_class = CElementsSidechainMutableTxIn
    _txout_class = CElementsSidechainMutableTxOut

    def blind(self, input_blinding_factors=(), input_asset_blinding_factors=(),
              input_assets=(), input_amounts=(),
              output_pubkeys=(), blind_issuance_assets=(), blind_issuance_tokens=(),
              auxiliary_generators=(), _rand_func=os.urandom):

        # based on Elements Core's BlindTransaction() function from src/blind.cpp
        # FIXME: add commit hash for reference

        assert len(self.vout) >= len(output_pubkeys)
        assert all(isinstance(p, CPubKey) for p in output_pubkeys)
        assert len(self.vin) + self.num_issuances >= len(blind_issuance_assets)
        assert all(isinstance(k, CKey) for k in blind_issuance_assets)
        assert len(self.vin) + self.num_issuances >= len(blind_issuance_tokens)
        assert all(isinstance(k, CKey) for k in blind_issuance_tokens)
        assert len(self.vin) == len(input_blinding_factors)
        assert all(isinstance(bf, Uint256) for bf in input_blinding_factors)
        assert len(self.vin) == len(input_asset_blinding_factors)
        assert all(isinstance(abf, Uint256) for abf in input_asset_blinding_factors)
        assert len(self.vin) == len(input_assets)
        assert all(isinstance(a, CAsset) for a in input_assets)
        assert len(self.vin) == len(input_amounts)

        output_blinding_factors = [None for _ in range(len(self.vout))]
        output_asset_blinding_factors = [None for _ in range(len(self.vout))]

        def result_tuple(nSuccessfullyBlinded):
            for i, bf in enumerate(output_blinding_factors):
                if bf is None:
                    output_blinding_factors[i] = Uint256()

            for i, bf in enumerate(output_asset_blinding_factors):
                if bf is None:
                    output_asset_blinding_factors[i] = Uint256()

            return nSuccessfullyBlinded, (output_blinding_factors, output_asset_blinding_factors)

        if auxiliary_generators:
            assert len(auxiliary_generators) >= len(self.vin)
            assert all(isinstance(ag, bytes) for ag in auxiliary_generators)
            assert all(len(ag) == 33 for ag in auxiliary_generators)

        output_blinding_factors = [None for _ in range(len(self.vout))]
        output_asset_blinding_factors = [None for _ in range(len(self.vout))]

        nBlindAttempts = 0
        nIssuanceBlindAttempts = 0
        nSuccessfullyBlinded = 0

        # Surjection proof prep

        # Needed to surj init, only matches to output asset matters, rest can be garbage
        surjectionTargets = [None for _ in range(len(self.vin)*3)]

        # Needed to construct the proof itself. Generators must match final transaction to be valid
        targetAssetGenerators = [None for _ in range(len(self.vin)*3)]

        # input_asset_blinding_factors is only for inputs, not for issuances(0 by def)
        # but we need to create surjection proofs against this list so we copy and insert 0's
        # where issuances occur.
        targetAssetBlinders = []

        totalTargets = 0

        for i in range(len(self.vin)):
            # For each input we either need the asset/blinds or the generator
            if input_assets[i].id.is_null():
                # If non-empty generator exists, parse
                if auxiliary_generators:
                    # Parse generator here
                    asset_generator = ctypes.create_string_buffer(SECP256K1_GENERATOR_SIZE)
                    result = secp256k1.secp256k1_generator_parse(
                        secp256k1_blind_context, asset_generator, auxiliary_generators[i])
                    if result != 1:
                        assert result == 0
                        return None
                else:
                    return None
            else:
                asset_generator = ctypes.create_string_buffer(SECP256K1_GENERATOR_SIZE)
                ret = secp256k1.secp256k1_generator_generate_blinded(
                    secp256k1_blind_context, asset_generator,
                    input_assets[i].id.data, input_asset_blinding_factors[i].data)
                assert ret == 1

            targetAssetGenerators[totalTargets] = asset_generator
            surjectionTargets[totalTargets] = input_assets[i]
            targetAssetBlinders.append(input_asset_blinding_factors[i])
            totalTargets += 1

            # Create target generators for issuances
            issuance = self.vin[i].assetIssuance

            if not issuance.is_null():
                if issuance.nAmount.is_commitment() or issuance.nInflationKeys.is_commitment():
                    return None

                # New Issuance
                if issuance.assetBlindingNonce.is_null():
                    blind_issuance = (len(blind_issuance_tokens) > i
                                      and blind_issuance_tokens[i].is_Valid())
                    entropy = generate_asset_entropy(self.vin[i].prevout, issuance.assetEntropy)
                    asset = calculate_asset(entropy)
                    token = calculate_reissuance_token(entropy, blind_issuance)
                else:
                    asset = calculate_asset(asset, issuance.assetEntropy)

                if not issuance.nAmount.is_null():
                    surjectionTargets[totalTargets] = asset
                    targetAssetGenerators[totalTargets] = ctypes.create_string_buffer(SECP256K1_GENERATOR_SIZE)
                    ret = secp256k1.secp256k1_generator_generate(
                        secp256k1_blind_context,
                        targetAssetGenerators[totalTargets], asset)
                    assert ret == 1
                    # Issuance asset cannot be blinded by definition
                    targetAssetBlinders.append(Uint256())
                    totalTargets += 1

                if not issuance.nInflationKeys.is_null():
                    assert not token.is_null()
                    surjectionTargets[totalTargets] = token
                    targetAssetGenerators[totalTargets] = ctypes.create_string_buffer(SECP256K1_GENERATOR_SIZE)
                    ret = secp256k1.secp256k1_generator_generate(
                        secp256k1_blind_context,
                        targetAssetGenerators[totalTargets], token.id.data)
                    assert ret == 1
                    # Issuance asset cannot be blinded by definition
                    targetAssetBlinders.append(Uint256())
                    totalTargets += 1

        if auxiliary_generators:
            # Process any additional targets from auxiliary_generators
            # we know nothing about it other than the generator itself
            for ag in auxiliary_generators[len(self.vin):]:
                targetAssetGenerators[totalTargets] = ctypes.create_string_buffer(SECP256K1_GENERATOR_SIZE)
                ret = secp256k1.secp256k1_generator_parse(
                    secp256k1_blind_context,
                    targetAssetGenerators[totalTargets], auxiliary_generators[i])
                if ret != 1:
                    assert ret == 0
                    return None

                surjectionTargets[totalTargets] = Uint256()
                targetAssetBlinders.append(Uint256())
                totalTargets += 1

        # Resize the target surjection lists to how many actually exist
        assert totalTargets == len(targetAssetBlinders)
        assert all(elt is None for elt in surjectionTargets[totalTargets:])
        assert all(elt is None for elt in targetAssetGenerators[totalTargets:])
        surjectionTargets = surjectionTargets[:totalTargets]
        targetAssetGenerators = targetAssetGenerators[:totalTargets]

        # Total blinded inputs that you own (that you are balancing against)
        nBlindsIn = 0
        # Number of outputs and issuances to blind
        nToBlind = 0

        txinwitsize = len(self.wit.vtxinwit)
        txoutwitsize = len(self.wit.vtxoutwit)

        blinds = []
        assetblinds = []
        amounts_to_blind = []

        for nIn in range(len(self.vin)):
            if (
                not input_blinding_factors[nIn].is_null()
                or not input_asset_blinding_factors[nIn].is_null()
            ):
                if input_amounts[nIn] < 0:
                    return None
                blinds.append(input_blinding_factors[nIn])
                assetblinds.append(input_asset_blinding_factors[nIn])
                amounts_to_blind.append(input_amounts[nIn])
                nBlindsIn += 1

            # Count number of issuance pseudo-inputs to blind
            issuance = self.vin[nIn].assetIssuance
            if not issuance.is_null():
                # Marked for blinding
                if len(blind_issuance_assets) > nIn and blind_issuance_assets[nIn].is_valid():
                    if (
                        issuance.nAmount.is_explicit() and
                        (txinwitsize <= nIn
                         or len(self.wit.vtxinwit[nIn].issuanceAmountRangeproof) == 0)
                    ):
                        nToBlind += 1
                    else:
                        return None

                if len(blind_issuance_tokens) > nIn and blind_issuance_tokens[nIn].is_valid():
                    if (
                        issuance.nInflationKeys.is_explicit() and
                        (txinwitsize <= nIn
                         or len(self.wit.vtxinwit[nIn].inflationKeysRangeproof) == 0)
                    ):
                        nToBlind += 1
                    else:
                        return None

        for nOut, out_pub in enumerate(output_pubkeys):
            if out_pub.is_valid:
                # Keys must be valid and outputs completely unblinded or else call fails
                if (
                    not out_pub.is_fullyvalid
                    or not self.vout[nOut].nValue.is_explicit()
                    or not self.vout[nOut].nAsset.is_explicit()
                    or (txoutwitsize > nOut and not self.wit.vtxoutwit[nOut].is_null())
                    or self.vout[nOut].is_fee()
                ):
                    return None

                nToBlind += 1

        # First blind issuance pseudo-inputs
        for nIn, txin in enumerate(self.vin):
            asset_issuace_valid = len(blind_issuance_assets) > nIn and blind_issuance_assets[nIn].is_valid()
            token_issuance_valid = len(blind_issuance_tokens) > nIn and blind_issuance_tokens[nIn].is_valid()
            for nPseudo in range(2):
                if nPseudo == 0:
                    iss_valid = asset_issuace_valid
                else:
                    iss_valid = token_issuance_valid

                if iss_valid:
                    nBlindAttempts += 1

                    nIssuanceBlindAttempts += 1

                    issuance = self.vin[nIn].assetIssuance

                    # First iteration does issuance asset, second inflation keys
                    explicitValue = issuance.nInflationKeys if nPseudo else issuance.nAmount
                    if explicitValue.is_null():
                        continue

                    amount = explicitValue.to_amount()

                    amounts_to_blind.append(amount)

                    # Derive the asset of the issuance asset/token
                    if issuance.assetBlindingNonce.is_null():
                        entropy = generate_asset_entropy(self.vin[nIn].prevout, issuance.assetEntropy)
                        if nPseudo == 0:
                            asset = calculate_asset(entropy)
                        else:
                            assert token_issuance_valid
                            asset = calculate_reissuance_token(entropy, token_issuance_valid)
                    else:
                        if nPseudo == 0:
                            asset = calculate_asset(issuance.assetEntropy)
                        else:
                            # Re-issuance only has one pseudo-input maximum
                            continue

                    # Fill out the value blinders and blank asset blinder
                    blinds.append(Uint256(_rand_func(32)))
                    # Issuances are not asset-blinded
                    assetblinds.append(Uint256())

                    if nBlindAttempts == nToBlind:
                        # All outputs we own are unblinded, we don't support this type of blinding
                        # though it is possible. No privacy gained here, incompatible with secp api
                        return result_tuple(nSuccessfullyBlinded)

                    assert len(self.wit.vtxinwit) == len(self.vin)

                    txinwit = self.wit.vtxinwit[nIn]

                    # TODO Store the blinding factors of issuance

                    # Create unblinded generator.
                    (_, gen) = blind_asset(asset, assetblinds[-1])

                    # Create value commitment
                    (confValue, commit) = create_value_commitment(blinds[-1].data, gen, amount)

                    if nPseudo:
                        issuance.nInflationKeys = confValue
                    else:
                        issuance.nAmount = confValue

                    # nonce should just be blinding key
                    if nPseudo == 0:
                        nonce = Uint256(blind_issuance_assets[nIn].secret_bytes)
                    else:
                        nonce = Uint256(blind_issuance_tokens[nIn].secret_bytes)

                    # Generate rangeproof, no script committed for issuances
                    rangeproof = generate_rangeproof(
                        blinds, nonce, amount, CScript(), commit, gen, asset, assetblinds)

                    if nPseudo == 0:
                        txinwit.issuanceAmountRangeproof = rangeproof
                    else:
                        txinwit.inflationKeysRangeproof = rangeproof

                    # Successfully blinded this issuance
                    nSuccessfullyBlinded += 1

        # This section of code *only* deals with unblinded outputs
        # that we want to blind
        for nOut, out_pub in enumerate(output_pubkeys):
            if out_pub.is_fullyvalid:
                out = self.vout[nOut]
                nBlindAttempts += 1
                explicitValue = out.nValue
                amount = explicitValue.to_amount()
                asset = out.nAsset.to_asset()
                amounts_to_blind.append(amount)

                blinds.append(Uint256(_rand_func(32)))
                assetblinds.append(Uint256(_rand_func(32)))

                # Last blinding factor r' is set as -(output's (vr + r') - input's (vr + r')).
                # Before modifying the transaction or return arguments we must
                # ensure the final blinding factor to not be its corresponding -vr (aka unblinded),
                # or 0, in the case of 0-value output, insisting on additional output to blind.
                if nBlindAttempts == nToBlind:

                    # Can't successfully blind in this case, since -vr = r
                    # This check is assuming blinds are generated randomly
                    # Adversary would need to create all input blinds
                    # therefore would already know all your summed output amount anyways.
                    if nBlindAttempts == 1 and nBlindsIn == 0:
                        return result_tuple(nSuccessfullyBlinded)

                    blindedAmounts = (ctypes.c_uint64 * len(amounts_to_blind))(*amounts_to_blind)
                    assetblindptrs = (ctypes.c_char_p*len(assetblinds))()
                    for i, ab in enumerate(assetblinds):
                        assetblindptrs[i] = ab.data

                    # Last blind will be written to
                    # by secp256k1_pedersen_blind_generator_blind_sum(),
                    # so we need to convert it into mutable buffer beforehand
                    last_blind = ctypes.create_string_buffer(blinds[-1].data, len(blinds[-1].data))
                    blindptrs = (ctypes.c_char_p*len(blinds))()
                    for i, blind in enumerate(blinds[:-1]):
                        blindptrs[i] = blind.data

                    blindptrs[-1] = ctypes.cast(last_blind, ctypes.c_char_p)

                    # Check that number of blinds match. This is important
                    # because this number is used by
                    # secp256k1_pedersen_blind_generator_blind_sum() to get the
                    # index of last blind, and that blinding factor will be overwritten.
                    assert len(blindptrs) == nBlindAttempts + nBlindsIn

                    assert(len(amounts_to_blind) == len(blindptrs))

                    _immutable_check_hash = hashlib.sha256(b''.join(b.data for b in blinds)).digest()

                    # Generate value we intend to insert
                    ret = secp256k1.secp256k1_pedersen_blind_generator_blind_sum(
                        secp256k1_blind_context,
                        blindedAmounts, assetblindptrs, blindptrs,
                        nBlindAttempts + nBlindsIn, nIssuanceBlindAttempts + nBlindsIn)

                    assert ret == 1

                    assert(_immutable_check_hash == hashlib.sha256(b''.join(b.data
                                                                            for b in blinds)).digest()),\
                        ("secp256k1_pedersen_blind_generator_blind_sum should not change "
                         "blinding factors other than the last one. Failing this assert "
                         "probably means that we supplied incorrect parameters to the function.")

                    blinds[-1] = Uint256(bytes(last_blind))

                    # Resulting blinding factor can sometimes be 0
                    # where inputs are the negations of each other
                    # and the unblinded value of the output is 0.
                    # e.g. 1 unblinded input to 2 blinded outputs,
                    # then spent to 1 unblinded output. (vr + r')
                    # becomes just (r'), if this is 0, we can just
                    # abort and not blind and the math adds up.
                    # Count as success(to signal caller that nothing wrong) and return early
                    if blinds[-1].is_null():
                        nSuccessfullyBlinded += 1
                        return result_tuple(nSuccessfullyBlinded)

                assert len(self.wit.vtxoutwit) == len(self.vout)

                txoutwit = self.wit.vtxoutwit[nOut]

                output_blinding_factors[nOut] = blinds[-1]
                output_asset_blinding_factors[nOut] = assetblinds[-1]

                # Blind the asset ID
                (confAsset, gen) = blind_asset(asset, assetblinds[-1])

                out.nAsset = confAsset

                # Create value commitment
                (confValue, commit) = create_value_commitment(blinds[-1].data, gen, amount)

                out.nValue = confValue

                # Generate nonce for rewind by owner
                (nonce, ephemeral_pubkey) = generate_output_rangeproof_nonce(output_pubkeys[nOut],
                                                                             _rand_func=_rand_func)
                out.nNonce = CConfidentialNonce(bytes(ephemeral_pubkey))

                # Generate rangeproof
                txoutwit.rangeproof = generate_rangeproof(
                    blinds, nonce, amount, out.scriptPubKey, commit, gen, asset, assetblinds)

                # Create surjection proof for this output
                if not surject_output(txoutwit, surjectionTargets, targetAssetGenerators,
                                      targetAssetBlinders, assetblindptrs, gen, asset,
                                      _rand_func=_rand_func):
                    continue

                # Successfully blinded this output
                nSuccessfullyBlinded += 1

        return result_tuple(nSuccessfullyBlinded)


class CElementsSidechainTransaction(CElementsSidechainTransactionCommon, CImmutableTransactionBase):
    _inverted_mutability_class = CElementsSidechainMutableTransaction
    _witness_class = CElementsSidechainTxWitness
    _txin_class = CElementsSidechainTxIn
    _txout_class = CElementsSidechainTxOut


class CElementsSidechainScript(CScriptBase):

    def is_unspendable(self):
        if len(self) == 0:
            return True
        return super(CElementsSidechainScript, self).is_unspendable()


class CoreElementsSidechainParams(CoreMainParams):
    NAME = 'sidechain/elements'
    TRANSACTION_CLASS = CElementsSidechainTransaction
    SCRIPT_CLASS = CElementsSidechainScript

    ct_exponent = 0
    ct_bits = 32


class ElementsSidechainParams(CoreElementsSidechainParams):
    RPC_PORT = 7041
    BASE58_PREFIXES = {'PUBKEY_ADDR': 235,
                       'SCRIPT_ADDR': 75,
                       'CONFIDENTIAL_ADDR': b'\x04',
                       'CONFIDENTIAL_PUBKEY_ADDR': b'\x04\xEB',
                       'CONFIDENTIAL_SCRIPT_ADDR': b'\x04\x4B',

                       # Note: these are the same as for Bitcoin testnet
                       'SECRET_KEY': 239,
                       'EXTENDED_PUBKEY': b'\x04\x35\x87\xCF',
                       'EXTENDED_PRIVKEY': b'\x04\x35\x83\x94'}

    BECH32_HRP = None


def generate_asset_entropy(prevout, contracthash):
    assert isinstance(prevout, COutPoint)
    assert isinstance(contracthash, (bytes, Uint256))
    if isinstance(contracthash, Uint256):
        contracthash = contracthash.data
    assert len(contracthash) == 32
    return Uint256(CSHA256().Write(prevout.GetHash()).Write(contracthash).Midstate())


def calculate_asset(entropy):
    assert isinstance(entropy, (bytes, Uint256))
    if isinstance(entropy, Uint256):
        entropy = entropy.data
    assert len(entropy) == 32
    return CAsset(CSHA256().Write(entropy).Write(Uint256().data).Midstate())


def calculate_reissuance_token(entropy, is_confidential):
    assert isinstance(entropy, (bytes, Uint256))
    if isinstance(entropy, Uint256):
        entropy = entropy.data
    assert len(entropy) == 32
    if is_confidential:
        k = Uint256.from_int(2)
    else:
        k = Uint256.from_int(1)
    return CAsset(CSHA256().Write(entropy).Write(k.data).Midstate())


def blind_asset(asset, assetblind):
    assert isinstance(asset, CAsset)
    assert isinstance(assetblind, Uint256)

    gen = ctypes.create_string_buffer(SECP256K1_GENERATOR_SIZE)
    ret = secp256k1.secp256k1_generator_generate_blinded(
        secp256k1_blind_context, gen, asset.id.data, assetblind.data)
    assert ret == 1
    result_commitment = ctypes.create_string_buffer(CConfidentialAsset._committedSize)
    ret = secp256k1.secp256k1_generator_serialize(
        secp256k1_blind_context, result_commitment, gen)
    assert ret == 1

    confAsset = CConfidentialAsset(bytes(result_commitment))
    assert confAsset.is_valid()

    return (confAsset, bytes(gen))


def create_value_commitment(blind, gen, amount):
    commit = ctypes.create_string_buffer(SECP256K1_PEDERSEN_COMMITMENT_SIZE)
    ret = secp256k1.secp256k1_pedersen_commit(
        secp256k1_blind_context, commit, blind, amount, gen)
    assert ret == 1
    result_commitment = ctypes.create_string_buffer(CConfidentialAsset._committedSize)
    ret = secp256k1.secp256k1_pedersen_commitment_serialize(
        secp256k1_blind_context, result_commitment, commit)
    assert ret == 1

    confValue = CConfidentialValue(bytes(result_commitment))
    assert confValue.is_valid()

    return (confValue, bytes(commit))


def generate_rangeproof(in_blinds, nonce, amount, scriptPubKey, commit, gen, asset, in_assetblinds):
    # NOTE: This is better done with typing module,
    # available since python3.5. but that means we will have
    # to add a dependency for python 3.4.
    # when we drop python3.4 support, we might use typing.
    assert isinstance(nonce, Uint256)
    assert isinstance(amount, int)
    assert isinstance(scriptPubKey, CElementsSidechainScript)
    assert isinstance(commit, bytes)
    assert len(commit) == SECP256K1_PEDERSEN_COMMITMENT_SIZE
    assert isinstance(asset, CAsset)

    # Note: the code only uses the single last elements of blinds and
    # assetblinds. We could require these elements to be passed explicitly,
    # but we will try to be close to original code.
    blind = in_blinds[-1]
    assert isinstance(blind, Uint256)
    assetblind = in_assetblinds[-1]
    assert isinstance(assetblind, Uint256)

    # Prep range proof
    nRangeProofLen = ctypes.c_size_t(5134)

    # TODO: smarter min_value selection

    rangeproof = ctypes.create_string_buffer(nRangeProofLen.value)

    # Compose sidechannel message to convey asset info (ID and asset blinds)
    assetsMessage = asset.id.data + assetblind.data

    ct_exponent = min(max(CoreElementsSidechainParams.ct_exponent, -1), 18)
    ct_bits = min(max(CoreElementsSidechainParams.ct_bits, 1), 51)
    # Sign rangeproof
    # If min_value is 0, scriptPubKey must be unspendable
    res = secp256k1.secp256k1_rangeproof_sign(
        secp256k1_blind_context,
        rangeproof, ctypes.byref(nRangeProofLen),
        0 if scriptPubKey.is_unspendable() else 1,
        commit, blind.data, nonce.data, ct_exponent, ct_bits,
        amount, assetsMessage, len(assetsMessage),
        None if len(scriptPubKey) == 0 else scriptPubKey,
        len(scriptPubKey),
        gen)

    assert res == 1

    return rangeproof[:nRangeProofLen.value]


# Creates ECDH nonce commitment using ephemeral key and output_pubkey
def generate_output_rangeproof_nonce(output_pubkey, _rand_func=os.urandom):
    # Generate ephemeral key for ECDH nonce generation
    ephemeral_key = CKey.from_secret_bytes(_rand_func(32))
    ephemeral_pubkey = ephemeral_key.pub
    assert len(ephemeral_pubkey) == CConfidentialNonce._committedSize
    # Generate nonce
    nonce = ephemeral_key.ECDH(output_pubkey)
    nonce = Uint256(hashlib.sha256(nonce).digest())
    return nonce, ephemeral_pubkey


# Create surjection proof
def surject_output(txoutwit, surjectionTargets, targetAssetGenerators, targetAssetBlinders,
                   assetblinds, gen, asset, _rand_func=os.urandom):

    nInputsToSelect = min(3, len(surjectionTargets))
    randseed = _rand_func(32)

    input_index = ctypes.c_size_t()
    proof_size = ctypes.c_int.in_dll(secp256k1, 'SECP256K1_SURJECTIONPROOF_RAW_SIZE').value
    proof = ctypes.create_string_buffer(proof_size)
    tag = asset.id.data

    ret = secp256k1.secp256k1_surjectionproof_initialize(
        secp256k1_blind_context, proof, ctypes.byref(input_index),
        build_aligned_data_array([st.id.data for st in surjectionTargets], 32),
        len(surjectionTargets),
        nInputsToSelect, tag, 100, randseed)

    if ret == 0:
        return False
    else:
        assert ret == 1

    ephemeral_input_tags_buf = build_aligned_data_array(targetAssetGenerators, 64)

    ret = secp256k1.secp256k1_surjectionproof_generate(
        secp256k1_blind_context, proof,
        ephemeral_input_tags_buf, len(targetAssetGenerators),
        gen, input_index, targetAssetBlinders[input_index.value].data, assetblinds[-1])

    assert ret == 1

    ret = secp256k1.secp256k1_surjectionproof_verify(
        secp256k1_blind_context, proof,
        ephemeral_input_tags_buf, len(targetAssetGenerators), gen)

    assert ret == 1

    expected_output_len = secp256k1.secp256k1_surjectionproof_serialized_size(
        secp256k1_blind_context, proof)
    output_len = ctypes.c_size_t(expected_output_len)
    serialized_proof = ctypes.create_string_buffer(output_len.value)
    secp256k1.secp256k1_surjectionproof_serialize(
        secp256k1_blind_context, serialized_proof, ctypes.byref(output_len), proof)
    assert output_len.value == expected_output_len

    txoutwit.surjectionproof = bytes(serialized_proof)

    return True


def unblind_confidential_pair(key, confValue, confAsset, nNonce, committedScript, rangeproof):
    assert isinstance(key, CKeyMixin)
    assert isinstance(confValue, CConfidentialValue)
    assert isinstance(confAsset, CConfidentialAsset)
    assert isinstance(nNonce, CConfidentialNonce)
    assert isinstance(committedScript, CScript)
    assert isinstance(rangeproof, bytes)

    # NOTE: we do not allow creation of invalid CKey instances,
    # so no key.is_valid check needed

    if len(rangeproof) == 0:
        return None

    ephemeral_key = CPubKey(nNonce.commitment)

    if len(nNonce.commitment) > 0 and not ephemeral_key.is_fullyvalid():
        return None

    # ECDH or not depending on if nonce commitment is non-empty
    if len(nNonce.commitment) > 0:
        if not ephemeral_key.is_fullyvalid():
            return None
        nonce = hashlib.sha256(key.ECDH(ephemeral_key)).digest()
    else:
        # Use blinding key directly, and don't commit to a scriptpubkey
        committedScript = CScript()
        nonce = key.secret_bytes

    # 32 bytes of asset type, 32 bytes of asset blinding factor in sidechannel
    msg_size = ctypes.c_size_t(64)
    # API-prescribed sidechannel maximum size,
    # though we only use 64 bytes
    msg = ctypes.create_string_buffer(4096)

    # If value is unblinded, we don't support unblinding just the asset
    if not confValue.is_commitment():
        return None

    observed_gen = ctypes.create_string_buffer(64)
    # Valid asset commitment?
    if confAsset.is_commitment():
        res = secp256k1.secp256k1_generator_parse(
            secp256k1_blind_context, observed_gen, confAsset.commitment)
        if res != 1:
            assert res == 0
            return None
    elif confAsset.is_explicit():
        res = secp256k1.secp256k1_generator_generate(
            secp256k1_blind_context, observed_gen, confAsset.to_asset().id.data)
        if res != 1:
            assert res == 0
            return None

    commit = ctypes.create_string_buffer(64)
    # Valid value commitment ?
    res = secp256k1.secp256k1_pedersen_commitment_parse(secp256k1_blind_context,
                                                        commit, confValue.commitment)
    if res != 1:
        assert res == 0
        return None

    blinding_factor_out = ctypes.create_string_buffer(32)
    asset_out = ctypes.create_string_buffer(32)

    min_value = ctypes.c_uint64()
    max_value = ctypes.c_uint64()
    amount = ctypes.c_uint64()

    res = secp256k1.secp256k1_rangeproof_rewind(
        secp256k1_blind_context,
        blinding_factor_out,
        ctypes.byref(amount),
        msg, ctypes.byref(msg_size),
        nonce,
        ctypes.byref(min_value), ctypes.byref(max_value),
        commit, rangeproof, len(rangeproof),
        committedScript or None, len(committedScript),
        observed_gen)

    if 0 == res:
        return None

    assert res == 1

    if not MoneyRange(amount.value):
        return None

    if msg_size.value != 64:
        return None

    asset_type = msg
    asset_blinder = msg[32:]
    recalculated_gen = ctypes.create_string_buffer(64)
    res = secp256k1.secp256k1_generator_generate_blinded(
        secp256k1_blind_context, recalculated_gen, asset_type, asset_blinder)
    if res != 1:
        assert res == 0
        return None

    # Serialize both generators then compare

    observed_generator = ctypes.create_string_buffer(33)
    derived_generator = ctypes.create_string_buffer(33)
    res = secp256k1.secp256k1_generator_serialize(
        secp256k1_blind_context, observed_generator, observed_gen)
    assert res == 1

    res = secp256k1.secp256k1_generator_serialize(
        secp256k1_blind_context, derived_generator, recalculated_gen)
    assert res == 1

    if observed_generator != derived_generator:
        return None

    return UnblindConfidentialPairResult(
        amount=amount.value, blinding_factor=Uint256(blinding_factor_out),
        asset=CAsset(asset_out), asset_blinding_factor=Uint256(msg[32:]))


def derive_blinding_key(blinding_derivation_key, script):
    assert isinstance(blinding_derivation_key, CKeyMixin)
    return CKey(hmac.new(blinding_derivation_key.secret_data, script,
                         hashlib.sha256).digest())


ZKPRangeproofInfo = namedtuple('ZKPRangeproofInfo', 'exp mantissa value_min value_max')
UnblindConfidentialPairResult = namedtuple('UnblindConfidentialPairResult',
                                           'amount blinding_factor asset asset_blinding_factor')


def get_chain_params(name):
    assert name == CoreElementsSidechainParams.NAME
    return CoreElementsSidechainParams, ElementsSidechainParams


__all__ = (
    'get_chain_params',
    'CAsset',
    'CAssetIssuance',
    'CConfidentialAsset',
    'CConfidentialValue',
    'CConfidentialNonce',
    'generate_asset_entropy',
    'calculate_asset',
    'calculate_reissuance_token',
)
