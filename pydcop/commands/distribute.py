# BSD-3-Clause License
#
# Copyright 2017 Orange
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.


"""
.. _pydcop_commands_distribute:

pydcop distribute
=================

``pydcop distribute``  distributes a the computations for a DCOP over a set
of agents.


Synopsis
--------
::

    pydcop distribute --dist <distribution_method>
                      [--graph <graph_model>]
                      [--algo <dcop_algorithm>] <dcop-files>

Description
-----------

Distributes the computation used to solve a DCOP.

The distribution obtained is written in yaml on standard output. It can also be
written into a file by using the ``--output`` global option. The
output file can be used as an input for
several commands that accept a distribution (e.g.
:ref:`orchestrator<pydcop_commands_orchestrator>`,
:ref:`solve<pydcop_commands_solve>`,
:ref:`run<pydcop_commands_run>`)

See Also
--------
:ref:`concepts_graph` and
:ref:`concepts_distribution`


Options
-------

``--dist <distribution_method>`` / ``-d <distribution_method>``
  The distribution algorithm (``oneagent``, ``adhoc``, ``ilp_fgdp``, etc.,
  see :ref:`concepts_distribution`).

``--algo <dcop_algorithm>`` / ``-a <dcop_algorithm>``
  The (optional) algorithm whose computations will be distributed. It is needed
  when the distribution depends on the computation's characteristics (which
  depend on the algorithm). For example when the distribution is based on
  computations footprint of communication load, the dcop algorithm is needed.

``--graph <graph_model>`` / ``-g <graph_model>``
  The (optional) computation graph model,
  one of ``factor_graph``, ``pseudotree``, ``constraints_hypergraph``
  (see. :ref:`concepts_graph`)
  The set of computation to distribute depends on the graph model used to
  represent the DCOP.
  When the ``--algo`` option is used, it is not required as the graph model
  can be deduced from the DCOP algorithm.

``<dcop-files>``
  One or several paths to the files containing the dcop. If several paths are
  given, their content is concatenated as used a the yaml definition for the
  DCOP.


Examples
--------

Distributing a DCOP for dsa, hence modeled as a constraints graph,
using the ``ilp_compref`` distribution method::

  pydcop distribute -d ilp_compref -a dsa \\
                    graph_coloring_10_4_15_0.1_capa_costs.yml

Distributing a DCOP modelled as a factor graph. The DCOP algorithm is not
required here as the ``oneagent`` distribution algorithm does not depends on
the computation's characteristics (as it simply assign one computation to each
agent)::

  dcop.py distribute --graph factor_graph \\
                     --dist oneagent graph_coloring1.yaml

The following command gives the same result. Here, we can deduce the required
graph model, as maxsum works on a factor graph::

  dcop.py distribute --algo maxsum \\
                     --dist oneagent graph_coloring1.yaml

Example output::

  cost: 0
  distribution:
    a1: [v3]
    a2: [diff_1_2]
    a3: [diff_2_3]
    a4: [v1]
    a5: [v2]
  inputs:
    algo: null
    dcop: [tests/instances/graph_coloring1.yaml]
    dist_algo: oneagent
    graph: factor_graph


"""

import logging
from importlib import import_module
import sys
import yaml

from pydcop.algorithms import list_available_algorithms
from pydcop.commands._utils import _error
from pydcop.dcop.yamldcop import load_dcop_from_file
from pydcop.distribution.objects import ImpossibleDistributionException

logger = logging.getLogger('pydcop.cli.distribute')


def set_parser(subparsers):

    algorithms = list_available_algorithms()

    parser = subparsers.add_parser('distribute',
                                   help='distribute a static dcop')
    parser.set_defaults(func=run_cmd)

    parser.add_argument('dcop_files', type=str, nargs='+', metavar='FILE',
                        help="dcop file(s)")

    parser.add_argument('-g', '--graph',
                        required=False,
                        choices=['factor_graph', 'pseudotree',
                                 'constraints_hypergraph'],
                        help='graphical model for dcop computations')

    parser.add_argument('-d', '--dist',
                        choices=['oneagent', 'adhoc', 'ilp_fgdp',
                                 'ilp_compref', 'heur_comhost'],
                        required=True,
                        help='algorithm for distributing the computation '
                             'graph')

    parser.add_argument('-a', '--algo',
                        choices=algorithms,
                        required=False,
                        help='Optional, only needed for '
                              'distribution methods that require '
                              'the memory footprint and '
                              'communication load for computations')


def run_cmd(args):
    logger.debug('dcop command "distribute" with arguments {} '.format(args))

    dcop_yaml_files = args.dcop_files
    logger.info('loading dcop from {}'.format(dcop_yaml_files))
    dcop = load_dcop_from_file(dcop_yaml_files)

    dist_module = load_distribution_module(args.dist)

    algo_module, graph_module = None, None
    if args.algo is not None:
        algo_module = load_algo_module(args.algo)

    if args.graph is not None:
        graph_module = load_graph_module(args.graph)
        # Check that the graph model and the algorithm are compatible:
        if algo_module is not None and algo_module.GRAPH_TYPE != args.graph:
            _error('Incompatible graph model and algorithm')
    elif algo_module is not None:
        graph_module = load_graph_module(algo_module.GRAPH_TYPE)
    else:
        _error('You must pass at leat --graph or --algo option')

    # Build factor-graph computation graph
    logger.info('Building computation graph for dcop {}'
                .format(dcop_yaml_files))
    cg = graph_module.build_computation_graph(dcop)

    logger.info('Distributing computation graph for dcop {}'
                .format(dcop_yaml_files))

    if algo_module is None:
        computation_memory = None
        communication_load = None
    else:
        computation_memory = algo_module.computation_memory
        communication_load = algo_module.communication_load

    try:
        distribution = dist_module\
            .distribute(cg, dcop.agents.values(),
                        hints=dcop.dist_hints,
                        computation_memory=computation_memory,
                        communication_load=communication_load)
        dist = distribution.mapping()

        if hasattr(dist_module, 'distribution_cost'):
            cost = dist_module.distribution_cost(
                distribution, cg, dcop.agents.values(),
                computation_memory=computation_memory,
                communication_load=communication_load)
        else:
            cost = None

        result = {
            'inputs': {
                'dist_algo': args.dist,
                'dcop': args.dcop_files,
                'graph': args.graph,
                'algo': args.algo,
            },
            'distribution': dist,
            'cost': cost
        }
        if args.output is not None:
            with open(args.output, encoding='utf-8', mode='w') as fo:
                fo.write(yaml.dump(result))
        print(yaml.dump(result))
        sys.exit(0)

    except ImpossibleDistributionException as e:
        result = {
            'status': 'FAIL',
            'error': str(e)
        }
        print(yaml.dump(result))
        sys.exit(2)


def load_distribution_module(dist):
    dist_module = None
    try:
        dist_module = import_module('pydcop.distribution.{}'.format(dist))
    except ImportError as e:
        _error('Could not find distribution method {}'.format(dist), e)
    return dist_module


def load_graph_module(graph):
    graph_module = None
    try:
        graph_module = import_module('pydcop.computations_graph.{}'.
                                     format(graph))
    except ImportError as e:
        _error('Could not find computation graph type: {}'.format(graph), e)
    return graph_module


def load_algo_module(algo):
    algo_module = None
    try:
        algo_module = import_module('pydcop.algorithms.{}'.format(algo))
    except ImportError as e:
        _error('Could not find dcop algorithm: {}'.format(algo), e)
    return algo_module
