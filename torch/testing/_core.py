"""
The testing package contains testing-specific utilities.
"""

import torch
import random
import math
import cmath
from typing import List, Optional, Tuple, Union
import operator

FileCheck = torch._C.FileCheck

__all__ = [
    "FileCheck",
    "all_types",
    "all_types_and",
    "all_types_and_complex",
    "all_types_and_complex_and",
    "all_types_and_half",
    "complex_types",
    "empty_types",
    "floating_and_complex_types",
    "floating_and_complex_types_and",
    "floating_types",
    "floating_types_and",
    "double_types",
    "floating_types_and_half",
    "get_all_complex_dtypes",
    "get_all_dtypes",
    "get_all_device_types",
    "get_all_fp_dtypes",
    "get_all_int_dtypes",
    "get_all_math_dtypes",
    "integral_types",
    "integral_types_and",
    "make_non_contiguous",
]

# Helper function that returns True when the dtype is an integral dtype,
# False otherwise.
# TODO: implement numpy-like issubdtype
def is_integral(dtype: torch.dtype) -> bool:
    # Skip complex/quantized types
    dtypes = [x for x in get_all_dtypes() if x not in get_all_complex_dtypes()]
    return dtype in dtypes and not dtype.is_floating_point

def is_quantized(dtype: torch.dtype) -> bool:
    return dtype in (torch.quint8, torch.qint8, torch.qint32, torch.quint4x2)

# Helper function that maps a flattened index back into the given shape
# TODO: consider adding torch.unravel_index
def _unravel_index(flat_index, shape):
    flat_index = operator.index(flat_index)
    res = []

    # Short-circuits on zero dim tensors
    if shape == torch.Size([]):
        return 0

    for size in shape[::-1]:
        res.append(flat_index % size)
        flat_index = flat_index // size

    if len(res) == 1:
        return res[0]

    return tuple(res[::-1])
# (bool, msg) tuple, where msg is None if and only if bool is True.
_compare_return_type = Tuple[bool, Optional[str]]

# Compares two tensors with the same size on the same device and with the same
# dtype for equality.
# Returns a tuple (bool, msg). The bool value returned is True when the tensors
# are "equal" and False otherwise.
# The msg value is a debug string, and is None if the tensors are "equal."
# NOTE: Test Framework Tensor 'Equality'
#   Two tensors are "equal" if they are "close", in the sense of torch.allclose.
#   The only exceptions are complex tensors and bool tensors.
#
#   Bool tensors are equal only if they are identical, regardless of
#   the rtol and atol values.
#
#   The `equal_nan` can be True or False, which maps to the True or False
#   in `torch.allclose`.
def _compare_tensors_internal(a: torch.Tensor, b: torch.Tensor, *, rtol, atol, equal_nan) -> _compare_return_type:
    debug_msg : Optional[str]
    # Integer (including bool) comparisons are identity comparisons
    # when rtol is zero and atol is less than one
    if (
        (is_integral(a.dtype) and rtol == 0 and atol < 1)
        or a.dtype is torch.bool
        or is_quantized(a.dtype)
    ):
        if (a == b).all().item():
            return (True, None)

        # Gathers debug info for failed integer comparison
        # NOTE: converts to long to correctly represent differences
        # (especially between uint8 tensors)
        identity_mask = a != b
        a_flat = a.to(torch.long).flatten()
        b_flat = b.to(torch.long).flatten()
        count_non_identical = torch.sum(identity_mask, dtype=torch.long)
        diff = torch.abs(a_flat - b_flat)
        greatest_diff_index = torch.argmax(diff)
        debug_msg = ("Found {0} different element(s) (out of {1}), with the greatest "
                     "difference of {2} ({3} vs. {4}) occuring at index "
                     "{5}.".format(count_non_identical.item(),
                                   a.numel(),
                                   diff[greatest_diff_index],
                                   a_flat[greatest_diff_index],
                                   b_flat[greatest_diff_index],
                                   _unravel_index(greatest_diff_index, a.shape)))
        return (False, debug_msg)

    # All other comparisons use torch.allclose directly
    if torch.allclose(a, b, rtol=rtol, atol=atol, equal_nan=equal_nan):
        return (True, None)

    # Gathers debug info for failed float tensor comparison
    # NOTE: converts to float64 to best represent differences
    a_flat = a.to(torch.float64 if not a.dtype.is_complex else torch.complex128).flatten()
    b_flat = b.to(torch.float64 if not a.dtype.is_complex else torch.complex128).flatten()
    diff = torch.abs(a_flat - b_flat)

    # Masks close values
    # NOTE: this avoids (inf - inf) oddities when computing the difference
    close = torch.isclose(a_flat, b_flat, rtol, atol, equal_nan)
    diff[close] = 0
    nans = torch.isnan(diff)
    num_nans = nans.sum()

    outside_range = (diff > (atol + rtol * torch.abs(b_flat))) | (diff == math.inf)
    count_outside_range = torch.sum(outside_range, dtype=torch.long)
    greatest_diff_index = torch.argmax(diff)
    debug_msg = ("With rtol={0} and atol={1}, found {2} element(s) (out of {3}) whose "
                 "difference(s) exceeded the margin of error (including {4} nan comparisons). "
                 "The greatest difference was {5} ({6} vs. {7}), which "
                 "occurred at index {8}.".format(rtol, atol,
                                                 count_outside_range + num_nans,
                                                 a.numel(),
                                                 num_nans,
                                                 diff[greatest_diff_index],
                                                 a_flat[greatest_diff_index],
                                                 b_flat[greatest_diff_index],
                                                 _unravel_index(greatest_diff_index, a.shape)))
    return (False, debug_msg)

# Checks if two scalars are equal(-ish), returning (True, None)
# when they are and (False, debug_msg) when they are not.
def _compare_scalars_internal(a, b, *, rtol: float, atol: float, equal_nan: Union[str, bool]) -> _compare_return_type:
    def _helper(a, b, s) -> _compare_return_type:
        # Short-circuits on identity
        if a == b or ((equal_nan in {"relaxed", True}) and a != a and b != b):
            return (True, None)

        # Special-case for NaN comparisions when equal_nan=False
        if not (equal_nan in {"relaxed", True}) and (a != a or b != b):
            msg = ("Found {0} and {1} while comparing" + s + "and either one "
                   "is nan and the other isn't, or both are nan and "
                   "equal_nan is False").format(a, b)
            return (False, msg)

        diff = abs(a - b)
        allowed_diff = atol + rtol * abs(b)
        result = diff <= allowed_diff

        # Special-case for infinity comparisons
        # NOTE: if b is inf then allowed_diff will be inf when rtol is not 0
        if ((cmath.isinf(a) or cmath.isinf(b)) and a != b):
            result = False

        msg = None
        if not result:
            if rtol == 0 and atol == 0:
                msg = f"{a} != {b}"
            else:
                msg = (
                    f"Comparing{s}{a} and {b} gives a "
                    f"difference of {diff}, but the allowed difference "
                    f"with rtol={rtol} and atol={atol} is "
                    f"only {allowed_diff}!"
                )
        return result, msg

    return _helper(a, b, " ")


def make_non_contiguous(tensor: torch.Tensor) -> torch.Tensor:
    if tensor.numel() <= 1:  # can't make non-contiguous
        return tensor.clone()
    osize = list(tensor.size())

    # randomly inflate a few dimensions in osize
    for _ in range(2):
        dim = random.randint(0, len(osize) - 1)
        add = random.randint(4, 15)
        osize[dim] = osize[dim] + add

    # narrow doesn't make a non-contiguous tensor if we only narrow the 0-th dimension,
    # (which will always happen with a 1-dimensional tensor), so let's make a new
    # right-most dimension and cut it off

    input = tensor.new(torch.Size(osize + [random.randint(2, 3)]))
    input = input.select(len(input.size()) - 1, random.randint(0, 1))
    # now extract the input of correct size from 'input'
    for i in range(len(osize)):
        if input.size(i) != tensor.size(i):
            bounds = random.randint(1, input.size(i) - tensor.size(i))
            input = input.narrow(i, bounds, tensor.size(i))

    input.copy_(tensor)

    # Use .data here to hide the view relation between input and other temporary Tensors
    return input.data


# Functions and classes for describing the dtypes a function supports
# NOTE: these helpers should correspond to PyTorch's C++ dispatch macros

# Verifies each given dtype is a torch.dtype
def _validate_dtypes(*dtypes):
    for dtype in dtypes:
        assert isinstance(dtype, torch.dtype)
    return dtypes

# class for tuples corresponding to a PyTorch dispatch macro
class _dispatch_dtypes(tuple):
    def __add__(self, other):
        assert isinstance(other, tuple)
        return _dispatch_dtypes(tuple.__add__(self, other))

_empty_types = _dispatch_dtypes(())
def empty_types():
    return _empty_types

_floating_types = _dispatch_dtypes((torch.float32, torch.float64))
def floating_types():
    return _floating_types

_floating_types_and_half = _floating_types + (torch.half,)
def floating_types_and_half():
    return _floating_types_and_half

def floating_types_and(*dtypes):
    return _floating_types + _validate_dtypes(*dtypes)

_floating_and_complex_types = _floating_types + (torch.cfloat, torch.cdouble)
def floating_and_complex_types():
    return _floating_and_complex_types

def floating_and_complex_types_and(*dtypes):
    return _floating_and_complex_types + _validate_dtypes(*dtypes)

_double_types = _dispatch_dtypes((torch.float64, torch.complex128))
def double_types():
    return _double_types

_integral_types = _dispatch_dtypes((torch.uint8, torch.int8, torch.int16, torch.int32, torch.int64))
def integral_types():
    return _integral_types

def integral_types_and(*dtypes):
    return _integral_types + _validate_dtypes(*dtypes)

_all_types = _floating_types + _integral_types
def all_types():
    return _all_types

def all_types_and(*dtypes):
    return _all_types + _validate_dtypes(*dtypes)

_complex_types = _dispatch_dtypes((torch.cfloat, torch.cdouble))
def complex_types():
    return _complex_types

_all_types_and_complex = _all_types + _complex_types
def all_types_and_complex():
    return _all_types_and_complex

def all_types_and_complex_and(*dtypes):
    return _all_types_and_complex + _validate_dtypes(*dtypes)

_all_types_and_half = _all_types + (torch.half,)
def all_types_and_half():
    return _all_types_and_half

def get_all_dtypes(include_half=True,
                   include_bfloat16=True,
                   include_bool=True,
                   include_complex=True,
                   include_complex32=False
                   ) -> List[torch.dtype]:
    dtypes = get_all_int_dtypes() + get_all_fp_dtypes(include_half=include_half, include_bfloat16=include_bfloat16)
    if include_bool:
        dtypes.append(torch.bool)
    if include_complex:
        dtypes += get_all_complex_dtypes(include_complex32)
    return dtypes

def get_all_math_dtypes(device) -> List[torch.dtype]:
    return get_all_int_dtypes() + get_all_fp_dtypes(include_half=device.startswith('cuda'),
                                                    include_bfloat16=False) + get_all_complex_dtypes()

def get_all_complex_dtypes(include_complex32=False) -> List[torch.dtype]:
    return [torch.complex32, torch.complex64, torch.complex128] if include_complex32 else [torch.complex64, torch.complex128]


def get_all_int_dtypes() -> List[torch.dtype]:
    return [torch.uint8, torch.int8, torch.int16, torch.int32, torch.int64]


def get_all_fp_dtypes(include_half=True, include_bfloat16=True) -> List[torch.dtype]:
    dtypes = [torch.float32, torch.float64]
    if include_half:
        dtypes.append(torch.float16)
    if include_bfloat16:
        dtypes.append(torch.bfloat16)
    return dtypes


def get_all_device_types() -> List[str]:
    return ['cpu'] if not torch.cuda.is_available() else ['cpu', 'cuda']
