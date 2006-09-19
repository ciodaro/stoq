# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2006 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s):   Henrique Romano  <henrique@async.com.br>
##              Johan Dahlin  <jdahlin@async.com.br>
##

import os
import glob
import inspect

from kiwi.dist import listpackages
from kiwi.python import namedAny
from twisted.trial import unittest
from zope.interface import implementedBy
from zope.interface.interface import InterfaceClass
from zope.interface.verify import verifyClass
from zope.interface.exceptions import Invalid

from stoqlib.domain.base import InheritableModelAdapter, ModelAdapter
from stoqlib.lib.component import Adapter

def _test_class(self, klass):
    for iface in implementedBy(klass):
        try:
            verifyClass(iface, klass)
        except Invalid, message:
            self.fail("%s(%s): %s" % (
                klass.__name__, iface.__name__, message))

def get_all_classes(package):
    for package in listpackages(package):
        # stoqlib.domain -> stoqlib/domain
        package = package.replace('.', os.path.sep)

        # List all python files in stoqlib/domain
        for filename in glob.glob(os.path.join(package, '*.py')):
            # stoqlib/domain/base.py -> stoqlib.domain.base
            modulename = filename[:-3].replace(os.path.sep, '.')
            module = namedAny(modulename)
            for name, klass in inspect.getmembers(module, inspect.isclass):
                yield klass

def get_interfaces_for_package(package):
    for klass in get_all_classes(package):
        if not implementedBy(klass):
            continue
        if not klass.__module__.startswith(package + '.'):
            continue
        if issubclass(klass, InterfaceClass):
            continue
        yield klass

def _create_adapter_test():
    # Create a dynamic test class which verifies that methods in all adapters
    # are defined in an interface.
    #
    # Exceptions:
    #    SQLObject create to_python/from_python
    #    Private methods, which has a name starting with _
    #    class methods
    #
    TODO = {
        'AbstractCheckBillAdapter': ' ',
        'AbstractPaymentGroup': ' ',
        'AbstractPaymentMethodAdapter': ' ',
        'AbstractRenegotiationAdapter': ' ',
        'ASalesPerson': 'Add get_status_string to IActive',
        'ASellable': ' ',
        'GiftCertificateAdaptToSellable': ' ',
        'PMAdaptToCardPM': ' ',
        'PMAdaptToCheckPM': ' ',
        'PMAdaptToFinancePM': ' ',
        'PMAdaptToGiftCertificatePM': ' ',
        'PMAdaptToMoneyPM': ' ',
        'PersonAdaptToBranch': ' ',
        'PersonAdaptToClient': ' ',
        'PersonAdaptToCreditProvider': ' ',
        'PersonAdaptToEmployee': ' ',
        'PersonAdaptToIndividual': ' ',
        'PersonAdaptToTransporter': ' ',
        'PersonAdaptToUser': ' ',
        'ProductAdaptToStorable': ' ',
        'PurchaseOrderAdaptToPaymentGroup': ' ',
        'SaleAdaptToPaymentGroup': ' ',
        'ServiceSellableItemAdaptToDelivery': ' ',
        }

    def _test_adapter(self, adapter):
        ifaces = implementedBy(adapter)
        if not ifaces:
            self.fail("%s does not provide any interfaces" % adapter)

        # Collect methods
        methods = []
        for name in adapter.__dict__.keys():
            if name.startswith('_'):
                continue
            value = getattr(adapter, name)
            if not inspect.ismethod(value):
                continue

            # Skip methods added by SQLObject
            if name in ('to_python', 'from_python'):
                continue

            # Skip classmethods
            if value.im_self is not None:
                # TODO: Only allow classmethods on base/abstract classes
                continue
            methods.append(name)

        # Remove methods which are part of an interface
        for iface in ifaces:
            for name, desc in iface.namesAndDescriptions():
                if not name in methods:
                    continue
                methods.remove(name)
        if methods:
            self.fail(
                "%s has public methods %s which are not part of an "
                "interface" % (adapter.__name__, ', '.join(methods)))
    namespace = dict(_test_adapter=_test_adapter)

    for klass in get_all_classes('stoqlib'):
        if not issubclass(klass, Adapter):
            continue

        # Skip bases classes
        if klass in (Adapter, InheritableModelAdapter, ModelAdapter):
            continue

        tname = klass.__name__
        name = 'test_' + tname
        func = lambda self, adapter=klass: self._test_adapter(adapter)
        func.__name__ = name
        if tname in TODO:
            func.todo = TODO[tname]
        namespace[name] = func

    return type('TestAdapters', (unittest.TestCase, ), namespace)

def _create_iface_test():
    TODO = {}
    namespace = dict(_test_class=_test_class)
    for iface in get_interfaces_for_package('stoqlib'):
        tname = iface.__name__
        name = 'test_' + tname
        func = lambda self, f=iface: self._test_class(f)
        func.__name__ = name
        if tname in TODO:
            func.todo = TODO[tname]
        namespace[name] = func

    return type('TestIfaces', (unittest.TestCase, ), namespace)

TestAdapters = _create_adapter_test()
TestIfaces = _create_iface_test()
