#!/usr/bin/env python

from datman.docopt import docopt
import sys, os, re, datetime
import datman.config as config
import datman.scanid as scanid
import datman.utils as utils
import logging, logging.handlers
import errno
import nibabel as nib
import dicom
import json

logger = logging.getLogger(os.path.basename(__file__))

def create_data_dict(dc):
    data = dict()
    maybe_data = [
        "Manufacturer",
        "ManufacturersModelName",
        "ImageType",
        "MagneticFieldStrength",
        "FlipAngle",
        "EchoTime",
        "RepetitionTime",
        # "PhaseEncodingDirection",
        "EffectiveEchoSpacing",
        "SliceTiming"]
    logger.info("Data to try and add to json file are: {}".format(str(maybe_data)))
    for name in maybe_data:
        try:
            data[name] = getattr(dc, name)
        except AttributeError:
            logger.warning("{} {} does not have {}".format(dc.PatientName, dc.SeriesDescription, name))
    return data

def create_json(file_path, data_dict):
    try:
        logger.info("Creating: {}".format(file_path))
        with open(file_path, "w+") as json_file:
            json.dump(data_dict, json_file, sort_keys=True, indent=4, separators=(',', ': '))
    except IOError:
        logger.critical('Failed to open: {}'.format(file_path), exc_info=True)
        sys.exit(1)

def create_dir(dir_path):
    if not os.path.isdir(dir_path):
        logger.info("Creating: {}".format(dir_path))
        try:
            os.mkdir(dir_path)
        except OSError:
            logger.critical('Failed creating: {}'.format(dir_path), exc_info=True)
            sys.exit(1)

def create_symlink(src, target_name, dest):
    create_dir(dest)
    target = dest + target_name
    target_path = os.path.join(dest, target_name)
    if not os.path.islink(target_path):
        logger.info('Linking:{} to {}'.format(src, target_name))
        try:
            os.symlink(src, target_path)
        except OSError as e:
            logger.warning('Failed creating symlink:{} --> {} with reason:{}'
                            .format(src, target_path, e.strerror))


def setup_logger(filepath, debug, config):

    logger.setLevel(logging.DEBUG)

    date = str(datetime.date.today())

    fhandler = logging.FileHandler(filepath +  date + "-opt-cu1-link.log", "w")
    fhandler.setLevel(logging.DEBUG)

    shandler = logging.StreamHandler()
    if debug:
        shandler.setLevel(logging.DEBUG)
    else:
        shandler.setLevel(logging.WARN)

    # server_ip = config.get_key('LOGSERVER')
    # server_handler = logging.handlers.SocketHandler(server_ip,
    #         logging.handlers.DEFAULT_TCP_LOGGING_PORT)
    # server_handler.setLevel(logging.CRITICAL)


    formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")

    fhandler.setFormatter(formatter)
    shandler.setFormatter(formatter)
    logger.addHandler(fhandler)
    logger.addHandler(shandler)
    #logger.addHandler(server_handler)


if __name__ == "__main__":
    cfg = config.config(study="OPT")
    study_logs = cfg.get_path('log')
    setup_logger(study_logs, "--debug" in sys.argv, cfg)

    study_nii = cfg.get_path('nii')
    logger.info("Study nii path is {}".format(study_nii))
    study_res = cfg.get_path('resources')
    logger.info("Study RESOURCES path is {}".format(study_res))
    study_dcm = cfg.get_path('dcm')
    logger.info("Study dcm path is {}".format(study_dcn))


    cu_sub = [sub for sub in os.listdir(study_res) if scanid.parse(sub).site == "CU1"]
    logger.info("CU1 subjects in the RESOURCES folder are : {}".format(str(cu_sub)))

    for sub in cu_sub:
        sub_res_name = study_res + sub + "/NII/"
        sub_dcm_name = study_dcm + sub[:-3] + "/"
        sub_nii_name = study_nii + sub + "/"
        logger.info("Subject {} nii folder is {}".format(sub, sub_nii_name))
        sub_res = os.listdir(sub_res_name)
        logger.info("Subject {} res folder is {}".format(sub, sub_res_name))
        sub_dcm = os.listdir(sub_dcm_name)
        logger.info("Subject {} dcm folder is {}".format(sub, sub_dcm_name))
        dcm_dict = { int(scanid.parse_filename(dcm)[2]) : dcm for dcm in sub_dcm }
        logger.info("Subject dicoms separated by series number are: {}".format(str(dcm_dict)))
        for item in sub_res:
            series_num =  int(item.split("_")[1][1:])
            sub_file_name = dcm_dict[series_num][:-4]
            ext = item[item.find("."):]
            nii_name = sub_file_name + ext
            src = sub_res_name + item
            create_symlink(src, nii_name, sub_nii_name )
            if ext == ".nii.gz":
                dc =  dicom.read_file(sub_dcm_name + dcm_dict[series_num])
                data_dict = create_data_dict(dc)
                file_path = sub_nii_name + sub_file_name + ".json"
                create_json(file_path, data_dict)
            #img = nib.load(sub_nii_name + nii_name)
