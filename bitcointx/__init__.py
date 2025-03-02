# Copyright (C) 2012-2018 The python-bitcoinlib developers
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

import os
import platform
import threading

from abc import ABCMeta
from contextlib import contextmanager
from collections import OrderedDict

import bitcointx.core
import bitcointx.core.script
import bitcointx.wallet
import bitcointx.util

# Note that setup.py can break if __init__.py imports any external
# dependencies, as these might not be installed when setup.py runs. In this
# case __version__ could be moved to a separate version.py and imported here.
__version__ = '1.0.2.dev0'

_thread_local = threading.local()


class ChainParamsMeta(ABCMeta):
    _required_attributes = (
        ('NAME', isinstance, str),
        ('RPC_PORT', isinstance, int),
        ('WALLET_DISPATCHER', issubclass,
         bitcointx.wallet.WalletCoinClassDispatcher),
    )
    _registered_classes = OrderedDict()
    _common_base_cls = None

    def __new__(cls, cls_name, bases, dct, name=None):
        """check that the chainparams class uses unique base class
        (no two chain param classes can share a base class).
        if `name=` parameter is specified in the class declaration,
        set NAME attribute on a class, and register that class in
        a table for lookup by name."""
        cls_instance = super().__new__(cls, cls_name, bases, dct)

        if len(bases):
            if not any(issubclass(b, cls._common_base_cls) for b in bases):
                raise TypeError(
                    '{} must be a subclass of {}'.format(
                        cls_name, cls._common_base_cls.__name__))
            for attr_name, checkfn, checkarg in cls._required_attributes:
                if attr_name not in dct:
                    # Attribute will be inherited from the base class
                    continue
                if not checkfn(dct[attr_name], checkarg):
                    raise TypeError(
                        '{}.{} failed {} check against {}'
                        .format(cls_name, attr_name, checkfn.__name__,
                                checkarg.__name__))

            if name is not None:
                if isinstance(name, str):
                    cls_instance.NAME = name
                    names = [name]
                elif isinstance(name, (list, tuple)):
                    names = name
                    cls_instance.NAME = names[0]
                else:
                    raise TypeError(
                        'name argument must be string, list, or tuple')
                for name in names:
                    if name in cls._registered_classes:
                        raise AssertionError(
                            'name {} is not allowed to be registered twice, '
                            'it was already registered by {} before'
                            .format(
                                name, cls._registered_classes[name].__name__))
                    cls._registered_classes[name] = cls_instance
        else:
            if cls._common_base_cls:
                raise TypeError(
                    '{} cannot be used with more than one class, '
                    '{} was here first'.format(cls.__name__,
                                               cls._common_base_cls))
            cls._common_base_cls = cls_instance

        return cls_instance


def find_chain_params(*, name=None):
    return ChainParamsMeta._registered_classes.get(name)


def get_registered_chain_params():
    result = []
    for param_cls in ChainParamsMeta._registered_classes.values():
        if param_cls not in result:
            result.append(param_cls)

    return result


class ChainParamsBase(metaclass=ChainParamsMeta):
    """All chain param classes must be a subclass of this class."""

    def get_confdir_path(self):
        """Return default location for config directory"""
        name = self.NAME.split('/')[0]

        if platform.system() == 'Darwin':
            return os.path.expanduser(
                '~/Library/Application Support/{}'.format(name.capitalize()))
        elif platform.system() == 'Windows':
            return os.path.join(os.environ['APPDATA'], name.capitalize())

        return os.path.expanduser('~/.{}'.format(name))

    def get_config_path(self):
        """Return default location for config file"""
        name = self.NAME.split('/')[0]
        return '{}/{}.conf'.format(self.get_confdir_path(), name)

    def get_datadir_extra_name(self):
        """Return appropriate dir name to find data for the chain,
        and .cookie file. For mainnet, it will be an empty string -
        because data directory is the same as config directory.
        For others, like testnet or regtest, it will differ."""
        name_parts = self.NAME.split('/')
        if len(name_parts) == 1:
            return ''
        return name_parts[1]

    @property
    def name(self):
        return self.NAME

    @property
    def readable_name(self):
        name_parts = self.NAME.split('/')
        name_parts[0] = name_parts[0].capitalize()
        return ' '.join(name_parts)


class BitcoinMainnetParams(ChainParamsBase,
                           name=('bitcoin', 'bitcoin/mainnet')):
    RPC_PORT = 8332
    WALLET_DISPATCHER = bitcointx.wallet.WalletBitcoinClassDispatcher


class BitcoinTestnetParams(BitcoinMainnetParams, name='bitcoin/testnet'):
    RPC_PORT = 18332
    WALLET_DISPATCHER = bitcointx.wallet.WalletBitcoinTestnetClassDispatcher


class BitcoinRegtestParams(BitcoinMainnetParams, name='bitcoin/regtest'):
    RPC_PORT = 18443
    WALLET_DISPATCHER = bitcointx.wallet.WalletBitcoinRegtestClassDispatcher


def get_current_chain_params():
    return _thread_local.params


@contextmanager
def ChainParams(params, **kwargs):
    """Context manager to temporarily switch chain parameters.
    """
    prev_params = get_current_chain_params()
    select_chain_params(params, **kwargs)
    try:
        yield
    finally:
        select_chain_params(prev_params)


def select_chain_params(params, **kwargs):
    """Select the chain parameters to use

    name is one of 'bitcoin', 'bitcoin/testnet', or 'bitcoin/regtest'

    Default chain is 'bitcoin'.

    The references to new parameter classes are saved in global variables
    that are thread-local, so changing chain parameters is thread-safe.
    """

    if isinstance(params, str):
        params_cls = find_chain_params(name=params)
        if params_cls is None:
            raise ValueError('Unknown chain %r' % params)
        params = params_cls(**kwargs)
    elif isinstance(params, type):
        params = params(**kwargs)

    if not isinstance(params, ChainParamsBase):
        raise ValueError('Supplied chain params is not a subclass of '
                         'ChainParamsBase')

    _thread_local.params = params
    bitcointx.util.activate_class_dispatcher(params.WALLET_DISPATCHER)


select_chain_params(BitcoinMainnetParams)

__all__ = (
    'ChainParamsBase',
    'BitcoinMainnetParams',
    'BitcoinTestnetParams',
    'BitcoinRegtestParams',
    'select_chain_params',
    'ChainParams',
    'get_current_chain_params',
    'get_registered_chain_params',
    'find_chain_params',
)
