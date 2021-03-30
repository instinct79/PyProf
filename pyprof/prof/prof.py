#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2020, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
This script reads the output (Python dictionary) created by parse.py.
For every kernel (line) in the input it determines
	module / class name e.g. torch.nn.functional
	operator name e.g. linear
	kernel parameters e.g. GEMM M, N, K, datatype
	bytes
	flops
	tensor core usage
	direction (fprop, bprop)
	and other things. Please see the tool usage.
"""

import re
from .usage import parseArgs
from .output import Output
from .utility import Utility
from .pointwise import Pointwise
from .convert import Convert
from .blas import *
from .embedding import Embedding
from .reduction import *
from .dropout import Dropout
from .softmax import *
#from pooling import * # work in progress
from .linear import Linear
from .optim import Adam
from .misc import *
from .conv import Conv
from .activation import Activation
from .index_slice_join_mutate import Cat, Reshape, MaskedScatter, Gather, Nonzero, IndexSelect, MaskedSelect, Stack
from .recurrentCell import RNNCell
from .recurrent import RNNBase
from .normalization import BatchNorm
from .randomSample import RandPerm
from .loss import MSELoss
from .data import Data
from .memory import OneZero, Fill, Full


def findFpropKernel(seq):
    #Find the last fprop kernel with the same seqId
    #First look at seqId and then at altSeqId
    for idx in reversed(range(len(kernels))):
        k = kernels[idx]
        if (seq in k['seqId']) and (k['dir'] == "fprop"):
            return idx

    for idx in reversed(range(len(kernels))):
        k = kernels[idx]
        if (seq in k['altSeqId']) and (k['dir'] == "fprop"):
            return idx

    return -1
    #print("Error: seqId {} not found.".format(seq), file=sys.stderr)
    #assert False


def foo(mod, op, d):
    if (op[0] == "linear"):
        xx = Linear(d)

    # rnn, lstm, gru (multiple layers, dropout, bidirectional)
    elif (mod[0] in ["LSTM", "GRU"]) and (op[0] == "forward"):
        xx = RNNBase(d)

    # rnncell, lstmcell, grucell
    elif (mod[0] in ["LSTMCell", "GRUCell"]) and (op[0] == "forward"):
        xx = RNNCell(d)

    elif op[0] in [
            "conv1d",
            "conv2d",
            "conv_transpose2d"
    ]:
        xx = Conv(d)

    elif (op[0] in Pointwise.ops):
        xx = Pointwise(d)

    elif (op[0] in Convert.ops):
        xx = Convert(d)

    elif op[0] in ["__matmul__", "matmul"]:
        xx = Matmul(d)

    elif op[0] == "embedding":
        xx = Embedding(d)

    #reduction
    elif op[0] == "sum":
        xx = Sum(d)

    elif op[0] == "mean":
        xx = Mean(d)

    elif op[0] == "norm":
        xx = Norm(d)

    elif op[0] == "dropout":
        xx = Dropout(d)

    #Index, Slice, Join, Mutate
    elif (op[0] == "stack"):
        xx = Stack(d)

    elif (op[0] == "cat"):
        xx = Cat(d)

    elif (op[0] == "reshape"):
        xx = Reshape(d)

    elif (op[0] == "masked_scatter_"):
        xx = MaskedScatter(d)

    elif (op[0] == "gather"):
        xx = Gather(d)

    elif (op[0] == "nonzero"):
        xx = Nonzero(d)

    elif (op[0] == "index_select"):
        xx = IndexSelect(d)

    elif (op[0] == "masked_select"):
        xx = MaskedSelect(d)

    #blas
    elif op[0] in ["addmm", "addmm_"]:
        xx = Addmm(d)

    elif op[0] == "mm":
        xx = Mm(d)

    elif op[0] == "bmm":
        xx = Bmm(d)

    #softmax
    elif op[0] == "softmax":
        xx = Softmax(d)

    elif op[0] == "log_softmax":
        xx = LogSoftmax(d)

    #loss
    elif op[0] == "mse_loss":
        xx = MSELoss(d)

    #optimizers
    elif op[0] == "adam":
        xx = Adam(d)

    #normalization
    elif op[0] == "batch_norm":
        xx = BatchNorm(d)

    #random
    elif op[0] == "randperm":
        xx = RandPerm(d)

    #memory
    elif op[0] in OneZero.ops:
        xx = OneZero(d)

    elif op[0] == "fill_":
        xx = Fill(d)

    elif op[0] == "full":
        xx = Full(d)

    #misc
    elif op[0] == "copy_":
        xx = Copy(d)

    elif op[0] == "clone":
        xx = Clone(d)

    elif op[0] == "contiguous":
        xx = Contiguous(d)

    elif op[0] == "any":
        xx = Any(d)

    elif (op[0] in Activation.ops):
        xx = Activation(d)

    elif op[0] == "to":
        xx = Convert(d)

    else:
        xx = Foo(d)

    return xx

def make_unique_op_name(layer_map, func_name, callid, op_type_list):
    '''
    make_unique_op_name()
        This function makes a unique layer name by mapping layer types
        to long layer names and incrementing the count only when new callids
        are observed.
        Data structure
          Hash indexed by layer_type
            - values are list of callids
            - count the number of callids in the list
               - that's the value to append to long layer
                 name, which makes it unique

    '''
    count = 0
    if func_name:
        callid_list = []
        op_type = op_type_list[0]
        if op_type in layer_map:
            callid_list = layer_map[op_type]
        else:
            layer_map[op_type] = callid_list
        if callid[0] not in callid_list:
            callid_list.append(callid[0])
        count = len(callid_list)
        assert count > 0, "Unexpected count {} for layer type {}".format(count, op_type)
        count = count - 1
        func_name = func_name + "/{}".format(count)

    return func_name


def main():
    #Read cmd line arguments
    cmdArgs = parseArgs()

    output = Output(cmdArgs)
    output.header()

    layer_name_map = {} # Stores instance count of each func stack name
    callid_to_unique_name_map = {}
    idx = -1
    #Read in all the kernel info
    for line in cmdArgs.file:
        idx += 1
        kernel = eval(line)
        assert (kernel)
        kernels.append(kernel)

        k = kernel
        d = Data(k)

        mod = k['mod']
        op = k['op']

        callid = k['callid']                ## Input node tracking
        input_callids = k['input_callids']  ## Input node tracking
        unique_name = k['unique_name']
        unique_name = make_unique_op_name(layer_name_map, unique_name, callid, op)
        if callid[0] not in callid_to_unique_name_map:
            callid_to_unique_name_map[callid[0]] = unique_name

        flops = 0
        params = {"na": "na"}
        tc = "na"
        bytes = 0

        if (d.dir == "bprop"):
            d.seqMarker = k['seqMarker']
            seq = k['seqId']
            if len(seq) > 1:
                pass
            seq = k['seqId'][:1]
            assert (len(seq) == 1), seq
            #assert (seq[0] != 0)
            assert (len(d.seqMarker) > 0)
            #If there is no useful marker associated, use the
            #sequence number to find the kernel from fprop
            if len(d.argMarker) == 0:
                index = findFpropKernel(seq[0])
                if index >= 0:
                    d.argMarker = kernels[index]['marker']
                    d.modMarker = kernels[index]['reprMarkers']
                    mod = kernels[index]['mod']
                    op = kernels[index]['op']

                    callid = kernels[index]['callid']                ## Input node tracking
                    input_callids = kernels[index]['input_callids']  ## Input node tracking

                    d.layer = kernels[index]['layer']
                    d.trace = kernels[index]['trace']
                    unique_name = kernels[index]['unique_name']
                    if callid[0] in callid_to_unique_name_map:
                        unique_name = callid_to_unique_name_map[callid[0]]

        # Check if marker has our annotations
        if len(d.argMarker) and Utility.hasNVTX(d.argMarker[0]):

            xx = foo(mod, op, d)

            bytes = xx.bytes()
            flops = xx.flops()
            op = xx.op()
            params = xx.params()
            tc = xx.tc()

        if type(op) is list:
            if len(op):
                op = op[0]
            else:
                op = ""

        if type(mod) is list:
            if len(mod):
                mod = mod[0]
            else:
                mod = ""

        d.index = idx + 1

        # The following 8 come from operator class functions.
        d.setParams(params)
        d.tc = tc
        d.flops = flops
        d.bytes = bytes
        d.mod = mod
        d.op = op
        d.callid = callid               ## Input node tracking
        d.input_callids = input_callids ## Input node tracking
        d.unique_name   = unique_name   ## Unique layer name

        output.data(d)


kernels = []
if __name__ == '__main__':
    main()
