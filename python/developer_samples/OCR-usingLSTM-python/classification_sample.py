#!/usr/bin/env python
"""
 Copyright (c) 2018 Intel Corporation

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""
from __future__ import print_function
import sys
import os
from argparse import ArgumentParser
import cv2
import numpy as np
import logging as log
import time
from openvino.inference_engine import IENetwork, IECore
from local_utils import log_utils, data_utils
from local_utils.config_utils import load_config
import os.path as ops
from easydict import EasyDict
from qarpo.demoutils import *

def build_argparser():
    parser = ArgumentParser()
    parser.add_argument("-m", "--model", help="Path to an .xml file with a trained model.", required=True, type=str)
    parser.add_argument("-i", "--input", help="Path to a folder with images or path to an image files", required=True,
                        type=str, nargs="+")
    parser.add_argument("-l", "--cpu_extension",
                        help="MKLDNN (CPU)-targeted custom layers.Absolute path to a shared library with the kernels "
                             "impl.", type=str, default=None)
    parser.add_argument("-pp", "--plugin_dir", help="Path to a plugin folder", type=str, default=None)
    parser.add_argument("-d", "--device",
                        help="Specify the target device to infer on; CPU, GPU, FPGA or MYRIAD is acceptable. Sample "
                             "will look for a suitable plugin for device specified (CPU by default)", default="CPU",
                        type=str)
    parser.add_argument("--labels", help="Labels mapping file", default=None, type=str)
    parser.add_argument("-nt", "--number_top", help="Number of top results", default=10, type=int)
    parser.add_argument("-ni", "--number_iter", help="Number of inference iterations", default=1000, type=int)
    parser.add_argument("-pc", "--perf_counts", help="Report performance counters", default=False, action="store_true")
    parser.add_argument("-o", "--output_dir", help="If set, it will write a video here instead of displaying it",
                        default=None, type=str)

    return parser


def main():
   
    job_id = os.environ['PBS_JOBID']
    codec = data_utils.TextFeatureIO(char_dict_path='Config/char_dict.json',ord_map_dict_path=r'Config/ord_map.json')
        
	
	
    log.basicConfig(format="[ %(levelname)s ] %(message)s", level=log.INFO, stream=sys.stdout)
    args = build_argparser().parse_args()
    model_xml = args.model
    model_bin = os.path.splitext(model_xml)[0] + ".bin"

    # Plugin initialization for specified device and load extensions library if specified
    ie = IECore()
    if args.cpu_extension and 'CPU' in args.device:
        ie.add_extension(args.cpu_extension, "CPU")
    # Read IR
    log.info("Loading network files:\n\t{}\n\t{}".format(model_xml, model_bin))
    net = IENetwork(model=model_xml, weights=model_bin)

    # if plugin.device == "CPU":
        # supported_layers = plugin.get_supported_layers(net)
        # not_supported_layers = [l for l in net.layers.keys() if l not in supported_layers]
        # if len(not_supported_layers) != 0:
            # log.error("Following layers are not supported by the plugin for specified device {}:\n {}".
                      # format(plugin.device, ', '.join(not_supported_layers)))
            # log.error("Please try to specify cpu extensions library path in sample's command line parameters using -l "
                      # "or --cpu_extension command line argument")
            # sys.exit(1)

    assert len(net.inputs.keys()) == 1, "Sample supports only single input topologies"
    assert len(net.outputs) == 1, "Sample supports only single output topologies"

    job_id = str(os.environ['PBS_JOBID'])
    infer_file = os.path.join(args.output_dir, 'i_progress.txt')
    log.info("Preparing input blobs")
    input_blob = next(iter(net.inputs))
    out_blob = next(iter(net.outputs))
    net.batch_size = len(args.input)

    # Read and pre-process input images
    n, c, h, w = net.inputs[input_blob].shape
    images = np.ndarray(shape=(n, c, h, w))
    for i in range(n):
        image = cv2.imread(args.input[i])
        if image.shape[:-1] != (h, w):
            log.warning("Image {} is resized from {} to {}".format(args.input[i], image.shape[:-1], (h, w)))
            image = cv2.resize(image, (w, h))
        image = image.transpose((2, 0, 1))  # Change data layout from HWC to CHW
        images[i] = image
    log.info("Batch size is {}".format(n))

    # Loading model to the plugin
    log.info("Loading model to the plugin")
    exec_net = ie.load_network(network=net, device_name=args.device)
    del net

    # Start sync inference
    log.info("Starting inference ({} iterations)".format(args.number_iter))
    infer_time = []
    t0 = time.time()
    print(args.number_iter)
    for i in range(args.number_iter):
        #t0 = time()
        res = exec_net.infer(inputs={input_blob: images})
        if i%10 == 0 or i==args.number_iter-1: 
            progressUpdate(infer_file, time.time()-t0, i+1, args.number_iter) 

        #infer_time.append((time()-t0)*1000)
    t1 = (time.time() - t0)*1000
    log.info("Average running time of one iteration: {} ms".format(np.average(np.asarray(infer_time))))
    if args.perf_counts:
        perf_counts = exec_net.requests[0].get_perf_counts()
        log.info("Performance counters:")
        print("{:<70} {:<15} {:<15} {:<15} {:<10}".format('name', 'layer_type', 'exet_type', 'status', 'real_time, us'))
        for layer, stats in perf_counts.items():
            print("{:<70} {:<15} {:<15} {:<15} {:<10}".format(layer, stats['layer_type'], stats['exec_type'],
                                                              stats['status'], stats['real_time']))

    # Processing output blob
    log.info("Processing output blob")
    res = res[out_blob]

    preds = res.argmax(2)
    preds = preds.transpose(1, 0)
    preds = np.ascontiguousarray(preds, dtype=np.int8).view(dtype=np.int8)
    values=codec.writer.ordtochar( preds[0].tolist())
    values=[v for i, v in enumerate(values) if i == 0 or v != values[i-1]]
    values = [x for x in values if x != ' ']
    res=''.join(values)
    print("The result is : " + res)
    
    #progress_file_path = os.path.join(args.output_dir,'i_progress_'+str(job_id)+'.txt')
    avg_time = round((t1/args.number_iter), 1)
    with open(os.path.join(args.output_dir, 'result.txt'), 'w') as f:
                #f.write(res + "\n Inference performed in " + str(np.average(np.asarray(infer_time))) + "ms") 
                f.write(res + "\n Inference performed in " + str(avg_time) + "ms") 
    with open(os.path.join(args.output_dir, 'stats.txt'), 'w') as f:
                f.write(str(avg_time)+'\n')
                f.write(str(args.number_iter)+'\n')


if __name__ == '__main__':
    sys.exit(main() or 0)
