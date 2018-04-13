#!/usr/bin/env python
"""Launch the DTIPrep pipeline"""

import datman.config
import datman.utils
import logging
import argparse
import os
import tempfile
import shutil
import sys
import subprocess
import re
import time

CONTAINER = '/scratch/mmanogaran/DTIPrep_container/dtiprep.img'

JOB_TEMPLATE = """
#####################################
#$ -S /bin/bash
#$ -wd /tmp/
#$ -N {name}
#$ -e {errfile}
#$ -o {logfile}
#####################################
echo "------------------------------------------------------------------------"
echo "Job started on" `date` "on system" `hostname`
echo "------------------------------------------------------------------------"
{script}
echo "------------------------------------------------------------------------"
echo "Job ended on" `date`
echo "------------------------------------------------------------------------"
"""

logging.basicConfig(level=logging.WARN,
                    format="[%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class QJob(object):
    def __init__(self, cleanup=True):
        self.cleanup = cleanup

    def __enter__(self):
        self.qs_f, self.qs_n = tempfile.mkstemp(suffix='.qsub')
        return self

    def __exit__(self, type, value, traceback):
        try:
            os.close(self.qs_f)
            if self.cleanup:
                os.remove(self.qs_n)
        except OSError:
            pass

    def run(self, code, name="DTIPrep", logfile="output.$JOB_ID", errfile="error.$JOB_ID", cleanup=True, slots=1):
        open(self.qs_n, 'w').write(JOB_TEMPLATE.format(script=code,
                                                       name=name,
                                                       logfile=logfile,
                                                       errfile=errfile,
                                                       slots=slots))
        logger.info('Submitting job')
        subprocess.call('qsub < ' + self.qs_n, shell=True)


def make_job(src_dir, dst_dir, protocol_dir, log_dir, scan_name, protocol_file=None, cleanup=True):
    # create a job file from template and use qsub to submit
    code = (
    "singularity run -B {src}:/input -B {dst}:/output -B {protocol}:/meta -B {log}:/logs {container} {scan_name}"
            .format(src=src_dir,
                    dst=dst_dir,
                    log=log_dir,
                    protocol=protocol_dir,
                    container=CONTAINER,
                    scan_name=scan_name))

    if protocol_file:
        code = code + ' --protocolFile={protocol_file}'.format(protocol_file=protocol_file)

    print code

    os.system(code)


    # with QJob() as qjob:
    #     #logfile = '{}:/tmp/output.$JOB_ID'.format(socket.gethostname())
    #     #errfile = '{}:/tmp/error.$JOB_ID'.format(socket.gethostname())
    #     logfile = os.path.join(log_dir, 'output.$JOB_ID')
    #     errfile = os.path.join(log_dir, 'error.$JOB_ID')
    #     logger.info('Making job DTIPrep for scan:{}'.format(scan_name))
    #     qjob.run(code=code, logfile=logfile, errfile=errfile)


def process_nrrd(src_dir, dst_dir, protocol_dir, log_dir, nrrd_file):
    scan, ext = os.path.splitext(nrrd_file[0])

    # expected name for the output file
    out_file = os.path.join(dst_dir, scan + '_QCed' + ext)
    if os.path.isfile(out_file):
        logger.info('File:{} already processed, skipping.'.format(nrrd_file[0]))
        return

    protocol_file = 'dtiprep_protocol_' + nrrd_file[1] + '.xml'

    if not os.path.isfile(os.path.join(protocol_dir, protocol_file)):
        # fall back to the default name
        protocol_file = 'dtiprep_protocol.xml'

    if not os.path.isfile(os.path.join(protocol_dir, protocol_file)):
        logger.error('Protocol file not found for tag:{}. A default protocol dtiprep_protocol.xml can be used.'.format(
            nrrd_file[1]))

    nrrd_path = os.path.join(src_dir, nrrd_file[0])
    if os.path.islink(nrrd_path):
        real_path = os.path.realpath(nrrd_path)
        if not os.path.exists(real_path):
            logger.error('Link: {} is broken, skipping'.format(nrrd_file[0]))
            return
        temp_dir = tempfile.mkdtemp()
        shutil.copyfile(real_path, os.path.join(temp_dir, nrrd_file[0]))
        make_job(temp_dir, dst_dir, protocol_dir, log_dir, scan, protocol_file)
        shutil.rmtree(temp_dir)
    else:
        make_job(src_dir, dst_dir, protocol_dir, log_dir, scan, protocol_file)


def convert_nii(dst_dir, log_dir):
    """
    Inspects output directory for nrrds, and converts them to nifti for
    downstream pipelines.
    """
    for nrrd_file in filter(lambda x: '.nrrd' in x, os.listdir(dst_dir)):
        file_stem = os.path.splitext(nrrd_file)[0]
        nii_file = file_stem + '.nii.gz'
        bvec_file = file_stem + '.bvec'
        bval_file = file_stem + '.bval'

        if nii_file not in os.listdir(dst_dir):
            logger.info('converting {} to {}'.format(nrrd_file, nii_file))

            cmd = 'DWIConvert --inputVolume {d}/{nrrd} --allowLossyConversion --conversionMode NrrdToFSL --outputVolume {d}/{nii} --outputBVectors {d}/{bvec} --outputBValues {d}/{bval}'.format(
                d=dst_dir, nrrd=nrrd_file, nii=nii_file, bvec=bvec_file, bval=bval_file)
            rtn, msg = datman.utils.run(cmd, verbose=False)

            # only report errors for actual diffusion-weighted data with directions
            # since DWIConvert is noisy when converting non-diffusion data from nrrd
            # we assume if this conversion is broken then all other conversion must be
            # suspect as well -- jdv
            if '_QCed.nrrd' in nrrd_file and rtn != 0:
                logger.error('File:{} failed to convert to NII.GZ\n{}'.format(nrrd_file, msg))


def process_session(src_dir, out_dir, protocol_dir, log_dir, session, **kwargs):
    """Launch DTI prep on all nrrd files in a directory"""
    src_dir = os.path.join(src_dir, session)
    out_dir = os.path.join(out_dir, session)
    nrrds = [f for f in os.listdir(src_dir) if f.endswith('.nrrd')]

    if 'tags' in kwargs:
        tags = kwargs['tags']
    if not tags:
        tags = ['DTI']

    # filter for tags
    nrrd_dti = []
    for f in nrrds:
        try:
            _, tag, _, _ = datman.scanid.parse_filename(f)
        except datman.scanid.ParseException:
            continue
        tag_match = [re.search(t, tag) for t in tags]
        if any(tag_match):
            nrrd_dti.append((f, tag))

    if not nrrd_dti:
        logger.warning('No DTI nrrd files found for session:{}'.format(session))
        return
    logger.info('Found {} DTI nrrd files.'.format(len(nrrd_dti)))

    if not os.path.isdir(out_dir):
        try:
            os.mkdir(out_dir)
        except OSError:
            logger.error('Failed to create output directory:{}'.format(out_dir))
            return

    # dtiprep on nrrd files
    for nrrd in nrrd_dti:
        process_nrrd(src_dir, out_dir, protocol_dir, log_dir, nrrd)

    # convert output nrrd files to nifti
    convert_nii(out_dir, log_dir)

def create_command(session, args):
    study = args.study
    session = ' --session {}'.format(session)
    out_dir = ' --outDir {}'.format(args.outDir) if args.outDir else ''
    log_dir = ' --logDir {}'.format(args.logDir) if args.logDir else ''
    tags = ''
    if args.tags:
        for tag in args.tags:
            tags +=' --tag {}'.format(tag)
    quiet = ' --quiet'.format(args.quiet) if args.quiet else ''
    verbose = ' --verbose'.format(args.verbose) if args.verbose else ''

    options = ''.join([session, out_dir, log_dir, tags, quiet, verbose])
    cmd = "{} {} {}".format(__file__, options, study)
    return cmd

def submit_proc_dtiprep(sessions, args, cfg):
    with datman.utils.cd(args.outDir):
        for i, session in enumerate(sessions):
            cmd = create_command(session, args)
            logger.info("Queueing command: {}".format(cmd))
            job_name = 'dm_proc_dtiprep_{}_{}'.format(i, time.strftime("%Y%m%d-%H%M%S"))
            os.system(cmd)
            # datman.utils.submit_job(cmd, job_name, log_dir = args.logDir,
            #     system = cfg.system, cpu_cores=1,
            #     walltime='23:00:00', dryrun = False)

def main():
    parser = argparse.ArgumentParser("Run DTIPrep on a DTI File")
    parser.add_argument("study", help="Study")
    parser.add_argument("--session", dest="session", help="Session identifier")
    parser.add_argument("--outDir", dest="outDir", help="output directory")
    parser.add_argument("--logDir", dest="logDir", help="log directory")
    parser.add_argument("--tag", dest="tags",
        help="Tag to process, --tag can be specified more than once. Defaults to all tags containing 'DTI'",
        action="append")
    parser.add_argument("--quiet", help="Minimal logging", action="store_true")
    parser.add_argument("--verbose", help="Maximal logging", action="store_true")
    args = parser.parse_args()

    if args.quiet:
        logger.setLevel(logging.ERROR)
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    cfg = datman.config.config(study=args.study)

    nii_path = cfg.get_path('nii')
    nrrd_path = cfg.get_path('nrrd')
    meta_path = cfg.get_path('meta')

    if not args.outDir:
        args.outDir = cfg.get_path('dtiprep')

    if not os.path.isdir(args.outDir):
        logger.info("Creating output path:{}".format(args.outDir))
        try:
            os.mkdir(args.outDir)
        except OSError:
            logger.error('Failed creating output dir:{}'.format(args.outDir))
            sys.exit(1)

    if not args.logDir:
        args.logDir = os.path.join(args.outDir, 'logs')

    if not os.path.isdir(args.logDir):
        logger.info("Creating log dir:{}".format(args.logDir))
        try:
            os.mkdir(args.logDir)
        except OSError:
            logger.error('Failed creating log directory"{}'.format(args.logDir))

    if not os.path.isdir(nrrd_path):
        logger.error("Src directory:{} not found".format(nrrd_path))
        sys.exit(1)

    if not args.session:
        sessions = [d for d in os.listdir(nrrd_path) if os.path.isdir(os.path.join(nrrd_path, d))]
        submit_proc_dtiprep(sessions, args, cfg)
    else:
        process_session(nrrd_path, args.outDir, meta_path, args.logDir, args.session, tags=args.tags)

    #
    #
    # if not args.session:
    #     sessions = [d for d in os.listdir(nrrd_path) if os.path.isdir(os.path.join(nrrd_path, d))]
    # else:
    #     sessions = [args.session]
    #
    # for session in sessions:
    #     process_session(nrrd_path, args.outDir, meta_path, args.logDir, session, tags=args.tags)

if __name__ == '__main__':
    main()
