__all__ = ['Given', 'When', 'Then', 'And', 'run_steps']

import imp
import inspect
import os
import re
import parser
import traceback
import unittest
import sys
from nose.plugins import Plugin
from nose.plugins.skip import SkipTest
from nose.plugins.errorclass import ErrorClass, ErrorClassPlugin
from nose.selector import TestAddress
from freshen import parser

import logging
log = logging.getLogger('nose.plugins')

spec_registry = {}

def step_decorator(spec):
    """ Decorator to wrap step definitions in. Registers definition. """
    def wrapper(func):
        r = re.compile(spec)
        spec_registry[r] = func
        return func
    return wrapper

Given = step_decorator
When = step_decorator
Then = step_decorator
And = step_decorator

def run_steps(spec):
    """ Called from within step definitions to run other steps. """
    
    caller = inspect.currentframe().f_back
    line = caller.f_lineno - 1
    fname = caller.f_code.co_filename
    
    steps = parser.parse_steps(spec, fname, line)
    for s in steps:
        find_and_run_match(s)

class FreshenException(Exception):
    pass

class ExceptionWrapper(Exception):
    
    def __init__(self, e, step):
        self.e = e
        self.step = step

def format_step(step):
    p = os.path.relpath(step.src_file, os.getcwd())
    return '"%s" # %s:%d' % (step.match,
                             p,
                             step.src_line)

class UndefinedStep(Exception):
    
    def __init__(self, step):
        p = os.path.relpath(step.src_file, os.getcwd())
        super(UndefinedStep, self).__init__(format_step(step))

def find_and_run_match(step):
    """ Look up the step in the registry, then run it """
    result = None
    for r, f in spec_registry.iteritems():
        matches = r.match(step.match)
        if matches:
            if result:
                raise FreshenException("Ambiguous: %s" % step.match)
            result = f, matches
    
    if not result:
        raise UndefinedStep(step)
        
    try:
        if step.arg is not None:
            return result[0](step.arg, *result[1].groups())
        else:
            return result[0](*result[1].groups())
    except Exception, e:
        raise ExceptionWrapper(sys.exc_info(), step)


class FreshenTestCase(unittest.TestCase):

    def __init__(self, feature, scenario, before, after):
        self.feature = feature
        self.scenario = scenario
        self.before = before
        self.after = after
        
        self.description = feature.name + ": " + scenario.name
        super(FreshenTestCase, self).__init__()
    
    def setUp(self):
        if self.before:
            self.before()
    
    def runTest(self):
        for step in self.scenario.steps:
            res = find_and_run_match(step)
    
    def tearDown(self):
        if self.after:
            self.after()

def load_feature(fname):
    """ Load and parse a feature file. """

    fname = os.path.abspath(fname)
    path = os.path.dirname(fname)
    
    before = after = None
    try:
        info = imp.find_module("steps", [path])
        mod = imp.load_module("steps", *info)

        before = getattr(mod, 'before', None)
        after = getattr(mod, 'after', None)
    except ImportError:
        pass
    
    return parser.parse_file(fname), before, after


class FreshenErrorPlugin(ErrorClassPlugin):

    enabled = True
    undefined = ErrorClass(UndefinedStep,
                           label="UNDEFINED",
                           isfailure=False)

    def options(self, parser, env):
        # Forced to be on!
        pass


class FreshenNosePlugin(Plugin):
    
    name = "freshen"
    
    def wantDirectory(self, dirname):
        return True
    
    def wantFile(self, filename):
        return filename.endswith(".feature")
    
    def loadTestsFromFile(self, filename):
        feat, before, after = load_feature(filename)
        
        for sc in feat.iter_scenarios():
            yield FreshenTestCase(feat, sc, before, after)
    
    def loadTestsFromName(self, name, _=None):
        addr = TestAddress(name)
        if addr.filename.endswith(".feature") and os.path.isfile(addr.filename):
            result = self.loadTestsFromFile(addr.filename)
            if addr.call:
                yield [t for t in result][int(addr.call) - 1]
            else:
                for t in result:
                    yield t
    
    def describeTest(self, test):
        if isinstance(test.test, FreshenTestCase):
            return test.test.description

    def formatFailure(self, test, err):
        if isinstance(test.test, FreshenTestCase):
            ec, ev, tb = err
            if ec is ExceptionWrapper:
                orig_ec, orig_ev, orig_tb = ev.e
                return (orig_ec, str(orig_ev) + "\n\n>> in " + format_step(ev.step), orig_tb)
            else:
                return err
    
    formatError = formatFailure

