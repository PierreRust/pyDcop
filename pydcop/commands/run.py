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
.. _pydcop_commands_run:


pydcop run
==========

Running a (dynamic) DCOP

Synopsis
--------

::

  pydcop run

Description
-----------
the run command run a dcop, it is generally used for dynamic dcop where
various events can occur during the life of the system.


Options
-------

TODO


Examples
--------

::
    pydcop -v 2 run --algo dsa  \
                    --algo_params variant:B \
                    --distribution  dist.yaml \
                    --replica_dist  replica_dist.yaml \
                    --scenario scenario.yaml \
                    --replication dist_ucs_hostingcosts \
                    --collect_on period
                    --period 1
                    --run_metrics  run_dcop.csv
                    --end_metrics  several_runs.csv

"""
import json
import logging
import multiprocessing
import threading
import traceback
from functools import partial
from queue import Queue, Empty
from threading import Thread

import sys

from pydcop.algorithms import list_available_algorithms
from pydcop.commands._utils import _error, prepare_metrics_files, \
    _load_modules, build_algo_def, collect_tread, add_csvline
from pydcop.dcop.yamldcop import load_dcop_from_file, load_scenario_from_file
from pydcop.distribution.yamlformat import load_dist_from_file
from pydcop.infrastructure.run import run_local_thread_dcop, \
    run_local_process_dcop
from pydcop.replication.yamlformat import load_replica_dist, \
    load_replica_dist_from_file

logger = logging.getLogger('pydcop.cli.run')


def set_parser(subparsers):

    algorithms = list_available_algorithms()
    logger.debug('Available DCOP algorithms %s', algorithms)
    parser = subparsers.add_parser('run',
                                   help='run a dcop')

    parser.set_defaults(func=run_cmd)
    parser.set_defaults(on_timeout=on_timeout)
    parser.set_defaults(on_force_exit=on_force_exit)

    parser.add_argument('dcop_files', type=str, nargs='+',
                        help="dcop file")

    parser.add_argument('-a', '--algo', required=True,
                        choices=algorithms,
                        help='algorithm for solving the dcop')
    parser.add_argument('-p', '--algo_params',
                        type=str, nargs='*',
                        help='parameters for the algorithm , given as '
                             'name:value. Several parameters can be given.')

    parser.add_argument('-d', '--distribution', required=True,
                        help='distribution of the computations on agents, '
                             'as a yaml file ')

    # FIXME: allow loading replica dist from file and pass it to the
    # orchestrator
    #parser.add_argument('-r', '--replica_dist',
    #                    help='distribution of the computations replicas on '
    #                         'agents, as a yaml file ')

    parser.add_argument('-r', '--replication_method', required=True,
                        help='replication method')
    parser.add_argument('-k', '--ktarget', required=True, type=int,
                        help='Requested resiliency level')

    parser.add_argument('-s', '--scenario', required=True,
                        help='scenario file')

    parser.add_argument('-m', '--mode',
                        default='thread',
                        choices=['thread', 'process'],
                        help='run agents as threads or processes')

    # Statistics collection arguments:
    parser.add_argument('-c', '--collect_on',
                        choices=['value_change', 'cycle_change', 'period'],
                        default='value_change',
                        help='When should a "new" assignment be observed')
    parser.add_argument('--period', type=float,
                        default=None,
                        help='Period for collecting metrics. only available '
                             'when using --collect_on period. Defaults to 1 '
                             'second if not specified')
    parser.add_argument('--run_metrics', type=str,
                        default=None,
                        help="Use this option to regularly store the data "
                             "in a csv file.")
    parser.add_argument('--end_metrics', type=str,
                        default=None,
                        help="Use this option to append the metrics of the "
                             "end of the run to a csv file.")

    # TODO : remove, this should no be at this level
    parser.add_argument('--infinity', '-i', default=float('inf'),
                        type=float,
                        help='Argument to determine the value used for '
                             'infinity in case of hard constraints, '
                             'for algorithms that do not use symbolic '
                             'infinity. Defaults to 10 000')


dcop = None
orchestrator = None
INFINITY = None

collect_on = None
run_metrics = None
end_metrics = None


def run_cmd(args, timer):
    logger.debug('dcop command "run" with arguments {}'.format(args))

    global INFINITY
    INFINITY = args.infinity

    global collect_on
    collect_on = args.collect_on
    period = None
    if args.collect_on == 'period':
        period = 1 if args.period is None else args.period
    else:
        if args.period is not None:
            _error('Cannot use "period" argument when collect_on is not '
                   '"period"')

    csv_cb = prepare_metrics_files(args.run_metrics, args.end_metrics,
                                   collect_on)

    _, algo_module, graph_module = _load_modules(None, args.algo)

    global dcop
    logger.info('loading dcop from {}'.format(args.dcop_files))
    dcop = load_dcop_from_file(args.dcop_files)

    logger.info('Loading distribution from {}'.format(args.distribution))
    distribution = load_dist_from_file(args.distribution)

    # FIXME: load replica dist from file and pass to orchestrator
    # logger.info('Loading replica distribution from {}'.format(
    #     args.distribution))
    # replica_dist = load_replica_dist_from_file(args.replica_dist)
    # logger.info('Dcop distribution : %s', replica_dist)

    logger.info('loading scenario from {}'.format(args.scenario))
    scenario = load_scenario_from_file(args.scenario)

    logger.info('Building computation graph ')
    cg = graph_module.build_computation_graph(dcop)

    algo = build_algo_def(algo_module, args.algo, dcop.objective,
                         args.algo_params)

    # Setup metrics collection
    collector_queue = Queue()
    collect_t = Thread(target=collect_tread,
                       args=[collector_queue, csv_cb],
                       daemon=True)
    collect_t.start()

    global orchestrator
    if args.mode == 'thread':
        orchestrator = run_local_thread_dcop(algo, cg, distribution, dcop,
                                             INFINITY,
                                             collector=collector_queue,
                                             collect_moment=args.collect_on,
                                             period=period,
                                             replication=args.replication_method)
    elif args.mode == 'process':

        # Disable logs from agents, they are in other processes anyway
        agt_logs = logging.getLogger('pydcop.agent')
        agt_logs.disabled = True

        # When using the (default) 'fork' start method, http servers on agent's
        # processes do not work (why ?)
        multiprocessing.set_start_method('spawn')
        orchestrator = run_local_process_dcop(algo, cg, distribution, dcop,
                                              INFINITY,
                                              collector=collector_queue,
                                              collect_moment=args.collect_on,
                                              period=period)

    orchestrator.set_error_handler(_orchestrator_error)

    try:
        orchestrator.deploy_computations()
        orchestrator.start_replication(args.ktarget)
        if orchestrator.wait_ready():
            orchestrator.run(scenario)
        # orchestrator.run(scenario) # FIXME
    except Exception as e:
        logger.error(e, exc_info=1)
        print(e)
        for th in threading.enumerate():
            print(th)
            traceback.print_stack(sys._current_frames()[th.ident])
            print()
        orchestrator.stop_agents(5)
        orchestrator.stop()
        _results('ERROR', e)


def _orchestrator_error(e):
    print('Error in orchestrator: \n ', e)
    sys.exit(2)


def _results(status):
    """
    Outputs results and metrics on stdout and trace last metrics in csv
    files if requested.

    :param status:
    :return:
    """
    metrics = orchestrator.end_metrics()
    metrics['status'] = status
    global end_metrics, run_metrics
    if end_metrics is not None:
        add_csvline(end_metrics, collect_on, metrics)
    if run_metrics is not None:
        add_csvline(run_metrics, collect_on, metrics)
    print(json.dumps(metrics, sort_keys=True, indent='  '))


def on_timeout():
    if orchestrator is None:
        return
    # Stopping agents can be rather long, we need a big timeout !
    orchestrator.stop_agents(20)
    orchestrator.stop()
    _results('TIMEOUT')


def on_force_exit(sig, frame):
    if orchestrator is None:
        return
    orchestrator.stop_agents(5)
    orchestrator.stop()
    _results('STOPPED')
    for th in threading.enumerate():
        print(th)
        traceback.print_stack(sys._current_frames()[th.ident])
        print()
