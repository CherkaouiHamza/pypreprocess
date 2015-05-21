"""
:Synopsis: Parser for pypreprocess .ini configuration files.
:Author: DOHMATOB Elvis Dopgima <gmdopp@gmail.com> <elvis.dohmatob@inria.fr>

"""

import os
import warnings
import glob
import re
from configobj import ConfigObj
import numpy as np
from subject_data import SubjectData
from io_utils import _expand_path, get_relative_path


def _del_nones_from_dict(some_dict):
    if isinstance(some_dict, dict):
        for k, v in some_dict.items():
            if v is None: del some_dict[k]
            else: _del_nones_from_dict(v)
    return some_dict


def _parse_job(config_file, **replacements):
    if not os.path.isfile(config_file):
        raise OSError("Configuration file '%s' doesn't exist!" % config_file)

    def sanitize(section, key):
        val = section[key]
        if isinstance(val, basestring):
            for k, v in replacements.items():
                val = val.replace("%" + k + "%", v)
        if key == "slice_order":
            if isinstance(val, basestring): return
        if isinstance(val, basestring):
            if val.lower() in ["true", "yes"]:
                # positive answers
                val = True
            elif val.lower() in ["false", "no"]:
                # negative answers
                val = False
            elif key == "slice_order": val = val.lower()
            elif val.lower() in ["none", "auto", "unspecified", "unknown",
                                 "default"]:
                # user wants the default value to be used
                val = None
            elif re.match("^ *?$", val):
                # empty value specified
                val = None
        if key in ["TR", "nslices", "ref_slice", "nsubjects", "nsessions",
                   "n_jobs"]:
            # evaluate param value if it's a formula
            val = None if val is None else eval(val)

        # BF: some users forget the "s" in "_sizes"
        if key in ["fwhm", "anat_fwhm", "anat_write_voxel_sizes",
                   "func_write_voxel_sizes", "slice_order",
                   "anat_write_voxel_size", "func_write_voxel_sizes"]:
            dtype = np.int if key == "slice_order" else np.float
            if not isinstance(val, basestring): val = ",".join(val)
            for x in "()[]": val = val.replace(x, "")
            val = list(np.fromstring(val, sep=",", dtype=dtype))
            if len(val) == 1: val = val[0]
        section[key] = val

    cobj = ConfigObj(config_file)
    cobj.walk(sanitize, call_on_sections=True)
    return cobj['config']


def _generate_preproc_pipeline(config_file, dataset_dir=None, output_dir=None,
                               options_callback=None, **kwargs):
    """
    Generate pipeline (i.e subject factor + preproc params) from
    config file.

    Returns
    -------
    subjects: list of `SubjectData` objects
        subject list

    preproc_params: dict
        preproc parameters

    """
    # read config file
    config_file = os.path.abspath(config_file)
    options = _parse_job(config_file, **kwargs)
    options = _del_nones_from_dict(options)

    # sanitize output_dir and dataset_dir
    for item in ["dataset_dir", "output_dir"]:
        if eval(item) is None:
            if not item in options:
                raise ValueError(
                    ("%s not specified (neither in config_file"
                     " nor in this function call)") % item)
    options["dataset_dir"] = dataset_dir = (
        dataset_dir if not dataset_dir is None else options["dataset_dir"])
    options["output_dir"] = output_dir = (
        output_dir if not output_dir is None else options["output_dir"])
    assert options["dataset_dir"]
    assert options["output_dir"]

    # load data from multiple dataset_dirs
    if not isinstance(dataset_dir, basestring):
        kwargs["output_dir"] = output_dir
        tmp = [_generate_preproc_pipeline(config_file, dataset_dir=dsd,
                                          options_callback=options_callback,
                                          **kwargs) for dsd in dataset_dir]
        subjects = [subject for x in tmp for subject in x[0]]
        return subjects, tmp[0][1]

    # invoke callback
    if options_callback:
        options = options_callback(options)
        if dataset_dir is None: dataset_dir = options.get("dataset_dir", None)
        if output_dir is None: output_dir = options.get("output_dir", None)

    # check dataset_dir
    dataset_dir = _expand_path(dataset_dir)
    if not os.path.isdir(dataset_dir):
        raise OSError("dataset_dir %s doesn't exist" % dataset_dir)

    # check output_dir
    output_dir = _expand_path(options["output_dir"], relative_to=dataset_dir)
    if output_dir is None:
        raise OSError(
            ("Could not expand 'output_dir' specified in %s: invalid"
             " path %s (relative to directory %s)") % (config_file,
                                                       options["output_dir"],
                                                       dataset_dir))

    # dataset description
    dataset_description = options.get("dataset_description", None)

    # preproc parameters
    preproc_params = {
        "spm_dir": options.get("spm_dir", None),
        "matlab_exec": options.get("matlab_exec", None),
        "report": options.get("report", True),
        "output_dir": output_dir,
        "dataset_id": options.get("dataset_id", dataset_dir),
        "n_jobs": options.get("n_jobs", None),
        "caching": options.get("caching", True),
        "cv_tc": options.get("cv_tc", True),
        "dataset_description": dataset_description,
        "slice_timing_software": options.get("slice_timing_software", "spm"),
        "realign_software": options.get("realign_software", "spm"),
        "coregister_software": options.get("coregister_software", "spm"),
        "smooth_software": options.get("smooth_software", "spm")}

    # delete orientation meta-data ?
    preproc_params['deleteorient'] = options.get(
        "deleteorient", False)

    # configure slice-timing correction node
    preproc_params["slice_timing"] = not options.get(
        "disable_slice_timing", False)
    # can't do STC without TR
    if preproc_params["slice_timing"]:
        preproc_params.update(dict((k, options.get(k, None))
                                   for k in ["TR", "TA", "slice_order",
                                             "interleaved"]))
        if preproc_params["TR"] is None:
            preproc_params["slice_timing"] = False

    # configure motion correction node
    preproc_params["realign"] = not options.get("disable_realign", False)
    if preproc_params["realign"]:
        preproc_params['realign_reslice'] = options.get("reslice_realign",
                                                        False)
        preproc_params['register_to_mean'] = options.get("register_to_mean",
                                                         True)

    # configure coregistration node
    preproc_params["coregister"] = not options.get("disable_coregister",
                                                   False)
    if preproc_params["coregister"]:
        preproc_params['coregister_reslice'] = options.get(
            "coregister_reslice")
        preproc_params['coreg_anat_to_func'] = not options.get(
            "coreg_func_to_anat", True)

    # configure tissue segmentation node
    preproc_params["segment"] = not options.get("disable_segment", False)
    preproc_params["newsegment"] = options.get(
        "newsegment", False) and preproc_params["segment"]

    # configure normalization node
    preproc_params["normalize"] = not options.get(
        "disable_normalize", False)

    # configure output voxel sizes
    for brain in ["func", "anat"]:
        k = "%s_write_voxel_size" % brain
        ks = k + "s"
        if k in options:
            assert not ks in options, (
                "Both %s and %s specified in ini file. Please use only one of "
                "them, they mean thesame thing!")
            options[ks] = options.pop(k)
        preproc_params[ks] = options.get(
            ks, [[3, 3, 3], [1, 1, 1]][brain == "anat"])

    # configure dartel
    preproc_params['dartel'] = options.get("dartel", False)
    preproc_params['output_modulated_tpms'] = options.get(
        "output_modulated_tpms", False)

    # configure smoothing node
    preproc_params["fwhm"] = options.get("fwhm", 0.)
    preproc_params["anat_fwhm"] = options.get("anat_fwhm", 0.)

    # how many subjects ?
    subjects = []
    nsubjects = options.get('nsubjects', np.inf)
    exclude_these_subject_ids = options.get(
        'exclude_these_subject_ids', [])
    include_only_these_subject_ids = options.get(
        'include_only_these_subject_ids', [])

    def _ignore_subject(subject_id):
        """
        Ignore given subject_id ?

        """
        if subject_id in exclude_these_subject_ids: return True
        elif len(include_only_these_subject_ids
                 ) and not subject_id in include_only_these_subject_ids:
            return True
        else: return False

    # subject data factory
    subject_dir_wildcard = os.path.join(
        dataset_dir, options.get("subject_dirs", "*"))
    sess_func_wildcards = [k for k in options.keys()
                           if re.match("session_.+_func", k)]
    sess_onset_wildcards = [k for k in options.keys()
                            if re.match("session_.+_onset", k)]
    sess_ids = [re.match("session_(.+)_func", session).group(1)
                for session in sess_func_wildcards]
    subject_data_dirs = sorted(glob.glob(subject_dir_wildcard))
    if not subject_data_dirs:
        warnings.warn("No subject directories found for wildcard: %s" % (
                subject_dir_wildcard))
        return [], preproc_params

    for subject_data_dir in subject_data_dirs:
        if len(subjects) == nsubjects:
            # we've had enough subjects already; end
            break
        subject_id = os.path.basename(subject_data_dir)
        if _ignore_subject(subject_id): continue
        subject_output_dir = os.path.join(output_dir, subject_id)

        # grab functional data
        func = []
        sess_output_dirs = []
        skip_subject = False
        onset = []
        for s, sess_func_wildcard in enumerate(sess_func_wildcards):
            o = None
            if s < len(sess_onset_wildcards):
                sess_onset_wildcard = sess_onset_wildcards[s]
                sess_onset_wildcard = options[sess_onset_wildcard]
                sess_onset_wildcard = os.path.join(subject_data_dir,
                                                  sess_onset_wildcard)
                sess_onset = sorted(glob.glob(sess_onset_wildcard))
                if len(sess_onset) > 1: raise ValueError
                if len(sess_onset) > 0: o = sess_onset[0]
            onset.append(o)
            sess_func_wildcard = options[sess_func_wildcard]
            sess_func_wildcard = os.path.join(subject_data_dir,
                                              sess_func_wildcard)
            sess_func = sorted(glob.glob(sess_func_wildcard))

            if not sess_func:
                warnings.warn(
                    ("subject %s: No func images found for"
                     " wildcard %s" % (subject_id, sess_func_wildcard)))
                skip_subject = True
                break
            sess_dir = os.path.dirname(sess_func[0])
            if len(sess_func) == 1:
                sess_func = sess_func[0]
            func.append(sess_func)

            # session output dir
            if os.path.basename(sess_dir) != os.path.basename(
                    subject_output_dir):
                sess_output_dir = os.path.join(subject_output_dir,
                                               get_relative_path(
                                                   subject_data_dir, sess_dir))
            else:
                sess_output_dir = subject_output_dir
            if not os.path.exists(sess_output_dir):
                os.makedirs(sess_output_dir)
            sess_output_dirs.append(sess_output_dir)

        # something is wrong with this guy, skip
        if skip_subject:
            warnings.warn("Skipping subject %s" % subject_id)
            continue

        # grab anat
        anat = None
        if not options.get("anat", None) is None:
            # grap anat file(s)
            anat_wildcard = os.path.join(subject_data_dir, options['anat'])
            anat = glob.glob(anat_wildcard)

            # skip subject if anat absent
            if len(anat) < 1:
                print (
                    "subject %s: anat image matching %s not found!; skipping"
                    " subject" % (subject_id, anat_wildcard))
                continue

            # we need just 1 anat volume
            anat = anat[0]
            anat_dir = os.path.dirname(anat)
        else:
            anat = None
            anat_dir = ""

        # anat output dir
        anat_output_dir = None
        if anat_dir:
            anat_output_dir = os.path.join(subject_output_dir,
                                           get_relative_path(subject_data_dir,
                                                             anat_dir))
            if not os.path.exists(anat_output_dir):
                os.makedirs(anat_output_dir)

        # make subject data
        subject_data = SubjectData(
            subject_id=subject_id, func=func, anat=anat,
            output_dir=subject_output_dir,
            session_output_dirs=sess_output_dirs,
            anat_output_dir=anat_output_dir,
            session_id=sess_ids,
            data_dir=subject_data_dir,
            onset=onset,
            TR=options.get('TR', None),
            drift_model='Cosine',
            hrf_model=options.get('hrf_model', 'Canonical With Derivative'),
            hfcut=options.get("hfcut", 128.),
            time_units=options.get("time_units", "seconds"))
        subjects.append(subject_data)

    if not subjects:
        warnings.warn(
            "No subjects globbed (dataset_dir=%s, subject_dir_wildcard=%s" % (
                dataset_dir, subject_dir_wildcard))

    return subjects, preproc_params

# this pseudo is better
import_data = _generate_preproc_pipeline


if __name__ == '__main__':
    from pypreprocess.reporting.base_reporter import dict_to_html_ul
    print dict_to_html_ul(_parse_job("job.conf"))
