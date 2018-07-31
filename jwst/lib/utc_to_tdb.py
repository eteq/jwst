import logging

from astropy.io import fits
from astropy.time import Time

"""
If we can't import timeconversion, functions in this module will be called
instead, using astropy.coordinates for the location of the solar-system
barycenter, and a linear function based on header keywords for the location
of JWST with respect to the Earth.
"""
try:
    from jwst import timeconversion
    USE_TIMECONVERSION = True
except Exception:
    import numpy as np
    import astropy.coordinates as acoord
    import astropy.constants
    USE_TIMECONVERSION = False

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

# This is an interface to call
#    timeconversion.compute_bary_helio_time(targetcoord, times)

def utc_tdb(filename):
    """Convert start, mid, end times from UTC to TDB.

    Parameters
    ----------
    filename: str
        Name of an input FITS file containing an INT_TIMES table.

    Returns
    -------
    tuple of three numpy arrays
        The time or times, expressed as MJD and with time scale TDB.
    """

    log.info("Processing file %s", filename)
    fd = fits.open(filename, mode="update")

    targetcoord = (fd[0].header["targ_ra"], fd[0].header["targ_dec"])

    try:
        hdunum = find_hdu(fd, "int_times")
    except RuntimeError as e:
        log.warning(str(e))
        fd.close()
        return (0., 0., 0.)

    # TT, MJD
    tt_start_times = to_tt(fd[hdunum].data.field("int_start_MJD_UTC"))
    tt_mid_times = to_tt(fd[hdunum].data.field("int_mid_MJD_UTC"))
    tt_end_times = to_tt(fd[hdunum].data.field("int_end_MJD_UTC"))

    # Function compute_bary_helio_time returns both barycentric and
    # heliocentric times; the "[0]" extracts the former.
    if USE_TIMECONVERSION:
        log.debug("Using the timeconversion module.")
        tdb_start_times = timeconversion.compute_bary_helio_time(
                                targetcoord, tt_start_times)[0]
        tdb_mid_times = timeconversion.compute_bary_helio_time(
                                targetcoord, tt_mid_times)[0]
        tdb_end_times = timeconversion.compute_bary_helio_time(
                                targetcoord, tt_end_times)[0]
    else:
        log.warning("Couldn't import the timeconversion module;")
        log.warning("using astropy.coordinates, and "
                    "JWST position and velocity keywords.")
        (eph_time, jwst_pos, jwst_vel) = get_jwst_keywords(fd)
        jwstpos = linear_pos(tt_start_times, eph_time, jwst_pos, jwst_vel)
        tdb_start_times = compute_bary_helio_time2(
                                targetcoord, tt_start_times, jwstpos)[0]
        jwstpos = linear_pos(tt_mid_times, eph_time, jwst_pos, jwst_vel)
        tdb_mid_times = compute_bary_helio_time2(
                                targetcoord, tt_mid_times, jwstpos)[0]
        jwstpos = linear_pos(tt_end_times, eph_time, jwst_pos, jwst_vel)
        tdb_end_times = compute_bary_helio_time2(
                                targetcoord, tt_end_times, jwstpos)[0]

    missing = False
    try:
        # TDB, MJD
        fd[hdunum].data.field("int_start_BJD_TDB")[:] = tdb_start_times.copy()
        fd[hdunum].data.field("int_mid_BJD_TDB")[:] = tdb_mid_times.copy()
        fd[hdunum].data.field("int_end_BJD_TDB")[:] = tdb_end_times.copy()
    except KeyError:
        missing = True

    fd.close()

    if missing:
        log.warning("One or more of the *BJD_TDB columns do not exist,")
        log.warning("so the INT_TIMES table was not updated.")

    return (tdb_start_times, tdb_mid_times, tdb_end_times)


def find_hdu(fd, extname):
    """Find the HDU with name extname.

    Parameters
    ----------
    fd: fits HDUList object
        List of HDUs in the input file.

    extname: str
        The extension name to be found in fd.

    Returns
    -------
    hdunum: int
        The HDU number (0 is the primary HDU) with EXTNAME = `extname`.
    """

    extname_uc = extname.upper()
    nhdu = len(fd)
    hdunum = None
    for i in range(1, nhdu):
        hdr = fd[i].header
        if "EXTNAME" in hdr and hdr["EXTNAME"] == extname_uc:
            if hdunum is not None:
                fd.close()
                raise RuntimeError("There are at least two HDUs with "
                                   "EXTNAME = {}; there should only be one."
                                   .format(extname_uc))
            hdunum = i 
    if hdunum is None or len(fd[hdunum].data) < 1:
        fd.close()
        raise RuntimeError("An {} table is required.".format(extname))

    return hdunum


def to_tt(utc_time):
    """Convert UTC to TT.

    Parameters
    ----------
    utc_time: float or numpy array
        Time or array of times, expressed as MJD with time scale UTC.

    Returns
    -------
    float or numpy array
        The time or times, expressed as MJD but with time scale TT.
    """

    temp = Time(utc_time, format="mjd", scale="utc")

    return temp.tt.value


""" ###
The following functions are only used if the timeconversion module could not
be imported, i.e. if USE_TIMECONVERSION is False.
"""

def get_jwst_position2(times, jwstpos, use_jpl_ephemeris=False):
    '''
    This returns the pair of relative positions from
    the barycenter and heliocenter to JWST in that order
    as a tuple of two arrays, each of shape (len(times), 3).
    '''

    t = Time(times, format="mjd", scale="tt")

    if use_jpl_ephemeris:
        from astropy.coordinates import solar_system_ephemeris
        solar_system_ephemeris.set('jpl')

    # Vectors from the solar-system barycenter to the center of the Earth.
    bary_earth = acoord.get_body_barycentric("earth", t)

    # Vectors from the solar-system barycenter to the center of the Sun.
    bary_sun = acoord.get_body_barycentric("sun", t)

    # Vectors from the center of the Sun to the center of the Earth.
    sun_earth = bary_earth - bary_sun

    # Convert to ordinary numpy arrays of 3-element vectors, in km.

    barysun_centerearth_pos = np.empty((len(t), 3), dtype=np.float64)
    barysun_centerearth_pos[:, 0] = bary_earth.x.si.value / 1000.
    barysun_centerearth_pos[:, 1] = bary_earth.y.si.value / 1000.
    barysun_centerearth_pos[:, 2] = bary_earth.z.si.value / 1000.

    centersun_centerearth_pos = np.empty((len(t), 3), dtype=np.float64)
    centersun_centerearth_pos[:, 0] = sun_earth.x.si.value / 1000.
    centersun_centerearth_pos[:, 1] = sun_earth.y.si.value / 1000.
    centersun_centerearth_pos[:, 2] = sun_earth.z.si.value / 1000.

    centerearth_jwst = jwstpos

    return (barysun_centerearth_pos + centerearth_jwst), \
            (centersun_centerearth_pos + centerearth_jwst)

def get_target_vector2(targetcoord):
    '''
    returns a unit vector given ra and dec that astropy coordinates can handle
    '''
    ra, dec = targetcoord
    coord = acoord.SkyCoord(ra, dec, distance=1, frame='icrs', unit='deg')
    cartcoord = coord.represent_as(acoord.CartesianRepresentation)
    x = cartcoord.x.value
    y = cartcoord.y.value
    z = cartcoord.z.value
    vector = np.array([x, y, z])
    return vector / np.sqrt((vector**2).sum())

def compute_bary_helio_time2(targetcoord, times, jwstpos,
                            use_jpl_ephemeris=False):
    '''
    The end point computational routine to compute the distance of JWST
    to the sun (or barycenter) projected onto the unit vector to the
    target and determine the relative light travel time that results.

    times is assumed to be MJD_TT, and it can be either a scalar value
    or an array.

    jwstpos should be a 3-element vector (list, tuple, ndarray), or an array
    of such vectors, in km.  The shape of jwstpos
    should be (3,) or (1, 3) or (len(times), 3).
    jwstpos overrides what would have been obtained from the JWST ephemeris.
    This is useful for regression testing.
    '''
    tvector = get_target_vector2(targetcoord)
    jwst_bary_vectors, jwst_sun_vectors = get_jwst_position2(
                        times, jwstpos, use_jpl_ephemeris)
    proj_bary_dist = (tvector * jwst_bary_vectors).sum(axis=-1)
    proj_sun_dist = (tvector * jwst_sun_vectors).sum(axis=-1)
    cspeed = astropy.constants.c.value / 1000.
    return times + proj_bary_dist / cspeed / 86400., times + proj_sun_dist / cspeed / 86400.


def get_jwst_keywords(fd):

    # TT MJD time at which JWST has position jwst_pos and velocity jwst_vel.
    eph_time = to_tt(fd[0].header['eph_time'])

    jwst_pos = np.array((fd[0].header['jwst_x'],
                         fd[0].header['jwst_y'],
                         fd[0].header['jwst_z']), dtype=np.float64)

    jwst_vel = np.array((fd[0].header['jwst_dx'],
                         fd[0].header['jwst_dy'],
                         fd[0].header['jwst_dz']), dtype=np.float64)

    return(eph_time, jwst_pos, jwst_vel)


def linear_pos(tt_times, eph_time, jwst_pos, jwst_vel):
    """Compute JWST position as a linear function of time.

    Parameters
    ----------
    tt_times: float or ndarray
        Time or array of times, expressed as MJD with time scale TT.

    eph_time: float
        The time (MJD, TT) at which JWST had the position and velocity
        given by `jwst_pos` and `jwst_vel` respectively.

    jwst_pos: ndarray
        A three-element vector in rectangular coordinates, giving the
        position of JWST in km at time `eph_time`.

    jwst_vel: ndarray
        A three-element vector in rectangular coordinates, giving the
        velocity of JWST in km/s at time `eph_time`.

    Returns
    -------
    ndarray
        An array of shape (n_times, 3), giving the position of JWST at
        each of the times in `tt_times`.  `n_times` is the length of
        `tt_times`, or 1 if `tt_times` is a float.
    """

    jwst_pos_2d = jwst_pos.reshape((3, 1))
    jwst_vel_2d = jwst_vel.reshape((3, 1))
    dt = tt_times - eph_time
    jwstpos = (jwst_pos_2d + dt * jwst_vel_2d).transpose()

    return jwstpos