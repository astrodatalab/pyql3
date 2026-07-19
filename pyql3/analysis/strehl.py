import numpy as np
import skimage.draw
import skimage.transform
import math
from astropy.modeling import models, fitting
from photutils.aperture import CircularAperture, aperture_photometry

def generate_pupil_mask(npix=256, du=None, pmrangl=0.0, pmsname='open'):
    if du is None:
        du = 2.124e-6 / (npix * 0.00994 / 206265)
        
    pmsstr = pmsname.upper()
    if pmsstr == 'OPEN':
        d = [0.49, 0.42, 0.350, 0.280, 0., 0.]
    elif pmsstr == 'LARGEHEX':
        d = [0.479, 0.4090, 0.3390, 0.2690, 0.1170, 0.0020]
    elif pmsstr == 'MEDIUMHEX':
        d = [0.471, 0.4010, 0.3310, 0.2610, 0.1250, 0.0030]
    elif pmsstr == 'SMALLHEX':
        d = [0.451, 0.3810, 0.3110, 0.2410, 0.1450, 0.0030]
    elif pmsstr == 'INCIRCLE':
        d = [0.392, 0.1325, 0.0030]
    else:
        d = [0.479, 0.4090, 0.3390, 0.2690, 0.1170, 0.0020]
        
    pms_pscl = 0.0899 # m/inch
    
    pupil = np.zeros((npix, npix), dtype=np.uint8)
    y, x = np.mgrid[0:npix, 0:npix]
    r = np.sqrt((x - (npix/2 - 0.5))**2 + (y - (npix/2 - 0.5))**2)
    
    if pmsstr == 'INCIRCLE':
        mask = (r*du*pms_pscl < d[0]) & (r*du*pms_pscl > d[1])
        pupil[mask] = 1
        
        v = np.array([
            [-d[2], 0],
            [d[2], 0],
            [d[2], d[0]*1.1],
            [-d[2], d[0]*1.1],
            [-d[2], 0]
        ]).T
        
        ang = np.deg2rad(60 * np.arange(6) + pmrangl)
        for i in range(6):
            rmat = np.array([
                [-np.sin(ang[i]), np.cos(ang[i])],
                [np.cos(ang[i]), np.sin(ang[i])]
            ])
            rv = npix/2 + np.dot(rmat, v) / (du * pms_pscl)
            rr, cc = skimage.draw.polygon(rv[1, :], rv[0, :], shape=(npix, npix))
            pupil[rr, cc] = 0
            
    else:
        s = (d[0] - d[1]) / np.cos(np.deg2rad(30))
        cos30 = np.cos(np.deg2rad(30))
        sin30 = np.sin(np.deg2rad(30))
        
        v0 = np.array([
            [d[5], d[4]/cos30 - d[5]*sin30],
            [d[5], d[2]/cos30 + d[5]*sin30],
            [s*cos30, d[2]/cos30 + s*sin30],
            [2*s*cos30, d[2]/cos30],
            [3*s*cos30, d[2]/cos30 + s*sin30],
            [d[0]*sin30, d[0]*cos30],
            [d[4]*sin30, d[4]*cos30],
            [d[5], d[4]/cos30 - d[5]*sin30]
        ]).T 
        
        v1 = v0.copy()
        v1[0, :] = -v1[0, :]
        
        ang = np.deg2rad(60 * np.arange(6) + pmrangl)
        for i in range(6):
            rmat = np.array([
                [-np.sin(ang[i]), np.cos(ang[i])],
                [np.cos(ang[i]), np.sin(ang[i])]
            ])
            rv0 = npix/2 + np.dot(rmat, v0) / (du * pms_pscl)
            rv1 = npix/2 + np.dot(rmat, v1) / (du * pms_pscl)
            
            rr, cc = skimage.draw.polygon(rv0[1, :], rv0[0, :], shape=(npix, npix))
            pupil[rr, cc] = 1
            rr, cc = skimage.draw.polygon(rv1[1, :], rv1[0, :], shape=(npix, npix))
            pupil[rr, cc] = 1
            
        if pmsstr == 'OPEN':
            mask = r*du < 1.30
            pupil[mask] = 0
            
    return pupil

def generate_psf(npix=256, pos=(0.0, 0.0), camname='0.020', effwave=2.12450, pmrangl=0.0):
    camstr = str(camname).strip()
    if camstr == '0.020': pscl = 0.020 / 206265
    elif camstr == '0.035': pscl = 0.035 / 206265
    elif camstr == '0.050': pscl = 0.050 / 206265
    elif camstr == '0.100': pscl = 0.100 / 206265
    else: pscl = 0.009942 / 206265
    
    tmp = pscl * 12.0 / (effwave * 1e-6)
    rpfac = max(1, 2**math.ceil(math.log2(tmp)))
    pscl1 = pscl / rpfac
    npix1 = int(npix * rpfac)
    du = (effwave * 1e-6) / (npix1 * pscl1)
    
    rdfac = max(1, 2**math.ceil(math.log2(du / 0.10)))
    npix2 = int(npix1 * rdfac)
    du = (effwave * 1e-6) / (npix2 * pscl1)
    
    pupil = generate_pupil_mask(npix=npix2, du=du, pmrangl=pmrangl, pmsname='largehex')
    
    uu = np.tile(np.arange(npix2), (npix2, 1))
    vv = np.tile(np.arange(npix2)[:, np.newaxis], (1, npix2))
    rpos = np.array(pos) * rpfac - 0.5
    
    phase = 2 * np.pi * (uu * rpos[0] + vv * rpos[1]) / npix2
    wavefront = pupil * np.exp(1j * phase)
    
    fft_res = np.fft.fft2(wavefront)
    rpsf = np.fft.fftshift(np.abs(fft_res)**2)
    
    start_idx = npix2//2 - npix1//2
    end_idx = npix2//2 + npix1//2
    sub_rpsf = rpsf[start_idx:end_idx, start_idx:end_idx]
    
    factors = (rdfac, rdfac)
    psf = skimage.transform.downscale_local_mean(sub_rpsf, factors)
    
    return psf / np.sum(psf)

def calculate_strehl(image, pos, skyval=0.0, photrad=0.5, camname='0.020', effwave=2.12450):
    """
    Computes OSIRIS Strehl ratio.
    pos: (x, y) 0-indexed center
    photrad: Photometric radius in arcsec
    """
    camstr = str(camname).strip()
    if camstr == '0.020': pscl = 0.020
    elif camstr == '0.035': pscl = 0.035
    elif camstr == '0.050': pscl = 0.050
    elif camstr == '0.100': pscl = 0.100
    else: pscl = 0.020
    
    # 1. Theoretical fwhm in arcsec
    fwhm0 = (206265. * effwave * 1e-6 / 10.0)
    
    xc, yc = pos
    psfsz = 256
    
    # 2. Generate Theoretical PSF
    # IDL code generated PSF at precise offset relative to integer grid
    int_xc = int(round(xc))
    int_yc = int(round(yc))
    
    # Offset of star from center of the integer pixel
    offset_x = xc - int_xc
    offset_y = yc - int_yc
    
    psf = generate_psf(npix=psfsz, pos=(-offset_x, -offset_y), camname=camname, effwave=effwave)
    psf_center = (psfsz//2, psfsz//2)
    
    # 3. Measure photometry
    ap_radius = photrad / pscl
    
    ap_star = CircularAperture((xc, yc), r=ap_radius)
    phot_table = aperture_photometry(image - skyval, ap_star)
    star_flux = phot_table['aperture_sum'][0]
    
    ap_psf = CircularAperture(psf_center, r=ap_radius)
    psf_phot_table = aperture_photometry(psf, ap_psf)
    psf_flux = psf_phot_table['aperture_sum'][0]
    
    # 4. Extract subimages for fitting
    box_size = int(round(4.0 * (fwhm0 / pscl)))
    if box_size % 2 == 0: box_size += 1
    half_box = box_size // 2
    
    # Subimage for star
    y_min, y_max = int_yc - half_box, int_yc + half_box + 1
    x_min, x_max = int_xc - half_box, int_xc + half_box + 1
    
    # Check bounds
    if y_min < 0 or x_min < 0 or y_max > image.shape[0] or x_max > image.shape[1]:
        return None # Too close to edge
        
    sim = image[y_min:y_max, x_min:x_max] - skyval
    
    # Subimage for PSF
    py_min = psf_center[1] - half_box
    py_max = psf_center[1] + half_box + 1
    px_min = psf_center[0] - half_box
    px_max = psf_center[0] + half_box + 1
    spsf = psf[py_min:py_max, px_min:px_max]
    
    # 5. Fit 1D Gaussian to radial profile
    yy, xx = np.mgrid[0:box_size, 0:box_size]
    # Center of subimage relative to true center
    cx_star = half_box + offset_x
    cy_star = half_box + offset_y
    r_star = np.sqrt((xx - cx_star)**2 + (yy - cy_star)**2)
    
    r_psf = np.sqrt((xx - half_box)**2 + (yy - half_box)**2)
    
    def fit_radial_profile(r_arr, val_arr, init_amp):
        fitter = fitting.LevMarLSQFitter()
        g_init = models.Gaussian1D(amplitude=init_amp, mean=0, stddev=2.0)
        # Lock mean to 0 (we are fitting exactly centered radial profile)
        g_init.mean.fixed = True 
        
        # Only fit inner core
        thresh = (fwhm0 / pscl) * 0.7
        mask = r_arr < thresh
        
        if not np.any(mask):
            return init_amp, 0.0
            
        r_fit = r_arr[mask].flatten()
        val_fit = val_arr[mask].flatten()
        
        g_fit = fitter(g_init, r_fit, val_fit)
        
        fitted_peak = g_fit.amplitude.value
        fitted_fwhm = g_fit.stddev.value * 2.355 * pscl
        return fitted_peak, fitted_fwhm
        
    star_peak, star_fwhm = fit_radial_profile(r_star, sim, np.max(sim))
    psf_peak, psf_fwhm = fit_radial_profile(r_psf, spsf, np.max(spsf))
    
    if psf_peak <= 0 or psf_flux <= 0:
        return None
        
    strehl = (star_peak / star_flux) / (psf_peak / psf_flux)
    
    return {
        'strehl': strehl,
        'star_fwhm': star_fwhm,
        'psf_fwhm': psf_fwhm,
        'star_peak': star_peak,
        'psf_peak': psf_peak,
        'r_star': r_star.flatten() * pscl,
        'val_star': sim.flatten() / star_peak if star_peak > 0 else sim.flatten(),
        'r_psf': r_psf.flatten() * pscl,
        'val_psf': spsf.flatten() / psf_peak if psf_peak > 0 else spsf.flatten()
    }
