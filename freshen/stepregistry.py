#-*- coding: utf-8 -*-
import imp
import logging
import re
import os
import sys
import traceback
from itertools import chain

__all__ = ['Given', 'When', 'Then', 'Before', 'After', 'AfterStep', 'BeforeAll', 'AfterAll', 'Transform', 'NamedTransform']
__unittest = 1

log = logging.getLogger('freshen')

class AmbiguousStepImpl(Exception):

    def __init__(self, step, impl1, impl2):
        self.step = step
        self.impl1 = impl1
        self.impl2 = impl2
        super(AmbiguousStepImpl, self).__init__('Ambiguous: "%s"\n %s\n %s' % (step.match,
                                                                              impl1.get_location(),
                                                                              impl2.get_location()))

class UndefinedStepImpl(Exception):

    def __init__(self, step):
        self.step = step
        super(UndefinedStepImpl, self).__init__('"%s" # %s' % (step.match, step.source_location()))

class StepImpl(object):

    def __init__(self, step_type, spec, func):
        self.step_type = step_type
        self.spec = spec
        self.func = func
        self.named_transforms = []

    def apply_named_transform(self, name, pattern, transform):
        if name in self.spec:
            self.spec = self.spec.replace(name, pattern)
            self.named_transforms.append(transform)
            if hasattr(self, 're_spec'):
                del self.re_spec

    def run(self, *args, **kwargs):
        return self.func(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

    def match(self, match):
        if not hasattr(self, 're_spec'):
            self.re_spec = re.compile(self.spec)
        return self.re_spec.match(match)

    def get_location(self):
        code = self.func.func_code
        return "%s:%d" % (code.co_filename, code.co_firstlineno)

class HookImpl(object):

    def __init__(self, cb_type, func, tags=[]):
        self.cb_type = cb_type
        self.tags = tags
        self.func = func
        self.tags = tags
        self.order = 0

    def __repr__(self):
        return "<Hook: @%s %s(...)>" % (self.cb_type, self.func.func_name)

    def run(self, scenario):
        return self.func(scenario)

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

class TransformImpl(object):

    def __init__(self, spec_fragment, func):
        self.spec_fragment = spec_fragment
        self.re_spec = re.compile(spec_fragment)
        self.func = func

    def is_match(self, arg):
        if arg is None:
            return False
        return self.re_spec.match(arg) != None

    def transform_arg(self, arg):
        match = self.re_spec.match(arg)
        if match:
            return self.func(*match.groups())

    def __call__(self, *args, **kwargs):
        self.func(*args, **kwargs)

class NamedTransformImpl(TransformImpl):

    def __init__(self, name, in_pattern, out_pattern, func):
        super(NamedTransformImpl, self).__init__(out_pattern, func)
        self.name = name
        self.in_pattern = in_pattern
        self.out_pattern = out_pattern

    def apply_to_step(self, step):
        step.apply_named_transform(self.name, self.in_pattern, self)


class StepImplLoadException(Exception):
    def __init__(self, exc):
        self.exc = exc


class StepImplLoader(object):

    def __init__(self):
        self.modules = {}
        self.module_counter = 0

    def load_steps_impl(self, registry, path, module_names=None):
        """
        Load the step implementations at the given path, with the given module names. If
        module_names is None then the module 'steps' is searched by default.
        """

        if not module_names:
            module_names = ['steps']

        path = os.path.abspath(path)

        for module_name in module_names:
            mod = self.modules.get((path, module_name))

            if mod is None:
                #log.debug("Looking for step def module '%s' in %s" % (module_name, path))
                cwd = os.getcwd()
                if cwd not in sys.path:
                    sys.path.append(cwd)

                try:
                    actual_module_name = os.path.basename(module_name)
                    complete_path = os.path.join(path, os.path.dirname(module_name))
                    info = imp.find_module(actual_module_name, [complete_path])
                except ImportError:
                    #log.debug("Did not find step defs module '%s' in %s" % (module_name, path))
                    return
                
                try:
                    # Modules have to be loaded with unique names or else problems arise
                    mod = imp.load_module("stepdefs_" + str(self.module_counter), *info)
                except:
                    exc = sys.exc_info()
                    raise StepImplLoadException(exc)

                self.module_counter += 1
                self.modules[(path, module_name)] = mod

            for item_name in dir(mod):
                item = getattr(mod, item_name)
                if isinstance(item, StepImpl):
                    registry.add_step(item.step_type, item)
                elif isinstance(item, HookImpl):
                    registry.add_hook(item.cb_type, item)
                elif isinstance(item, NamedTransformImpl):
                    registry.add_named_transform(item)
                elif isinstance(item, TransformImpl):
                    registry.add_transform(item)

class StepImplRegistry(object):

    def __init__(self, tag_matcher_class):
        self.steps = {
            'given': [],
            'when': [],
            'then': []
        }

        self.hooks = {
            'before': [],
            'after': [],
            'after_step': []
        }

        self.transforms = []
        self.named_transforms = []
        self.tag_matcher_class = tag_matcher_class

    def add_step(self, step_type, step):
        self.steps[step_type].append(step)
        for named_transform in self.named_transforms:
            named_transform.apply_to_step(step)

    def add_hook(self, hook_type, hook):
        self.hooks[hook_type].append(hook)

    def add_transform(self, transform):
        self.transforms.append(transform)

    def add_named_transform(self, named_transform):
        self.named_transforms.append(named_transform)
        for step in chain(*self.steps.values()):
            named_transform.apply_to_step(step)

    def _apply_transforms(self, arg, step):
        for transform in chain(step.named_transforms, self.transforms):
            if transform.is_match(arg):
                return transform.transform_arg(arg)
        return arg

    def find_step_impl(self, step):
        """
        Find the implementation of the step for the given match string. Returns the StepImpl object
        corresponding to the implementation, and the arguments to the step implementation. If no
        implementation is found, raises UndefinedStepImpl. If more than one implementation is
        found, raises AmbiguousStepImpl.
        
        Each of the arguments returned will have been transformed by the first matching transform
        implementation.
        """
        result = None
        for si in self.steps[step.step_type]:
            matches = si.match(step.match)
            if matches:
                if result:
                    raise AmbiguousStepImpl(step, result[0], si)

                args = [self._apply_transforms(arg, si) for arg in matches.groups()]
                result = si, args

        if not result:
            raise UndefinedStepImpl(step)
        return result

    def get_hooks(self, cb_type, tags=[]):
        hooks = [h for h in self.hooks[cb_type] if self.tag_matcher_class(h.tags).check_match(tags)]
        hooks.sort(cmp=lambda x, y: cmp(x.order, y.order))
        return hooks


def step_decorator(step_type):
    def decorator_wrapper(spec):
        """ Decorator to wrap step definitions in. Registers definition. """
        def wrapper(func):
            return StepImpl(step_type, spec, func)
        return wrapper
    return decorator_wrapper

def hook_decorator(cb_type):
    """ Decorator to wrap hook definitions in. Registers hook. """
    def decorator_wrapper(*tags_or_func):
        if len(tags_or_func) == 1 and callable(tags_or_func[0]):
            # No tags were passed to this decorator
            func = tags_or_func[0]
            return HookImpl(cb_type, func)
        else:
            # We got some tags, so we need to produce the real decorator
            tags = tags_or_func
            def d(func):
                return HookImpl(cb_type, func, tags)
            return d
    return decorator_wrapper

all_steps = {'before':[], 'after':[]}

def all_decorator(cb_type):
    def dec_before(f):
        all_steps['before'].append(f)
        return f

    def dec_after(f):
        all_steps['after'].append(f)
        return f
    return cb_type == 'before' and dec_before or dec_after

def run_all_hooks(cb_type):
    for f in all_steps[cb_type]:
        f()

def transform_decorator(spec_fragment):
    def wrapper(func):
        return TransformImpl(spec_fragment, func)
    return wrapper

def named_transform_decorator(name, in_pattern, out_pattern=None):
    if out_pattern is None: out_pattern = in_pattern
    def wrapper(func):
        return NamedTransformImpl(name, in_pattern, out_pattern, func)
    return wrapper

Given = step_decorator('given')
When = step_decorator('when')
Then = step_decorator('then')
Before = hook_decorator('before')
After = hook_decorator('after')
AfterStep = hook_decorator('after_step')
BeforeAll = all_decorator('before')
AfterAll = all_decorator('after')
Transform = transform_decorator
NamedTransform = named_transform_decorator
