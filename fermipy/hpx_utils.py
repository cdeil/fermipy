#!/usr/bin/env python
#

# Description
"""
Utilities for dealing with HEALPix projections and mappings
"""

import re

import healpy as hp
import numpy as np

import astropy.io.fits as pf
from astropy.coordinates import SkyCoord
from astropy.coordinates import Galactic,ICRS,FK5  

from fits_utils import read_energy_bounds

# This is an approximation of the size of HEALPix pixels (in degrees) 
# for a particular order.   It is used to convert from HEALPix to WCS-based
# projections
HPX_ORDER_TO_PIXSIZE = [32.0,16.0,8.0,4.0,2.0,1.0,
                        0.50,0.25,0.1,0.05,0.025,0.01,
                        0.005,0.002]
                            

def coords_to_vec(lon,lat):
    """ Converts longitute and latitude coordinates to a unit 3-vector 

    return array(3,n) with v_x[i],v_y[i],v_z[i] = directional cosines
    """
    phi = np.radians(lon)
    theta = (np.pi/2) - np.radians(lat)
    sin_t = np.sin(theta)
    cos_t = np.cos(theta)

    xVals = sin_t * np.cos(phi)
    yVals = sin_t * np.sin(phi)
    zVals = cos_t

    # Stack them into the output array
    out = np.vstack((xVals,yVals,zVals)).swapaxes(0,1)
    return out


def get_pixel_size_from_nside(nside):
    """ Returns an estimate of the pixel size from the HEALPix nside coordinate
    
    This just uses a lookup table to provide a nice round number for each
    HEALPix order. 
    """
    order = int(np.log2(nside))
    if order < 0 or order > 13:
        raise Exception('HEALPix order must be between 0 to 13 %i'%order)
        return 
    return HPX_ORDER_TO_PIXSIZE[order]


def make_hpx_to_wcs_mapping(hpx,wcs):
    """ Make the mapping data needed to from from HPX pixelization to a WCS-based array

    hpx     : The healpix mapping (an HPX object)
    wcs     : The wcs mapping (a pywcs.wcs object)

    returns:
      ipixs    :  array(nx,ny) of HEALPix pixel indices for each wcs pixel
      mult_val :  array(nx,ny) of 1./number of wcs pixels pointing at each HEALPix pixel
      npix     :  tuple(nx,ny) with the shape of the wcs grid
    """
    npix = (int(wcs.wcs.crpix[0]*2),int(wcs.wcs.crpix[1]*2))
    pix_crds = np.dstack(np.meshgrid(np.arange(npix[0]),
                                     np.arange(npix[1]))).swapaxes(0,1).reshape((npix[0]*npix[1],2))
    sky_crds = wcs.wcs_pix2world(pix_crds, 0)
 
    sky_crds *= np.radians(1.)
    sky_crds[0:,1] = (np.pi/2) - sky_crds[0:,1]

    ipixs = hp.pixelfunc.ang2pix(hpx.nside,sky_crds[0:,1],sky_crds[0:,0],hpx.nest)

    # Here we are counting the number of HEALPix pixels each WCS pixel points to;
    # this could probably be vectorized by filling a histogram.
    d_count = {}
    for ipix in ipixs:
        if d_count.has_key(ipix):
            d_count[ipix] += 1
        else:
            d_count[ipix] = 1
        pass

    # Here we are getting a multiplicative factor that tells use how to split up
    # the counts in each HEALPix pixel (by dividing the corrsponding WCS pixels 
    # by the number of associated HEALPix pixels).
    # This could also likely be vectorized.
    mult_val = np.ones(ipixs.shape)
    for i,ipix in enumerate(ipixs):
        mult_val[i] /= d_count[ipix]
        pass
    
    ipixs = ipixs.reshape(npix).T.flatten()
    mult_val = mult_val.reshape(npix).T.flatten()
    return ipixs,mult_val,npix
    

def match_hpx_pixel(nside,nest,nside_pix,ipix_ring):
    """
    """
    print 'match_nside',nside,nside_pix
    ipix_in = np.arange(12*nside*nside)
    vecs = hp.pix2vec(nside,ipix_in,nest)
    pix_match = hp.vec2pix(nside_pix,vecs[0],vecs[1],vecs[2]) == ipix_ring
    return ipix_in[pix_match]
   

class Map_Base(object):
    """ Abstract representation of a 2D or 3D counts map.

    We have both WCS and HEALPix based implemenations
    """
    
    def __init__(self,counts):
        self._counts = counts

    @property
    def counts(self):
        return self._counts



class HPX(object):
    """ Encapsulation of basic healpix map parameters """

    def __init__(self,nside,nest,coordsys,order=-1,region=None,ebins=None):
        """ C'tor 

        nside     : HEALPix nside parameter, the total number of pixels is 12*nside*nside
        nest      : bool, True -> 'NESTED', False -> 'RING' indexing scheme
        coordsys  : Coordinate system, 'CEL' | 'GAL'
        """
        if nside >= 0:
            if order >= 0:
                raise Exception('Specify either nside or oder, not both.')
            else:
                self._nside = nside
                self._order = -1
        else:
            if order >= 0:
                self._nside = 2**order
                self._order = order
            else:
                raise Exception('Specify either nside or oder, not both.')
        self._nest = nest
        self._coordsys = coordsys
        self._region = region
        self._maxpix = 12*self._nside*self._nside
        if self._region:
            self._ipix = self.get_index_list(self._nside,self._nest,self._region)
            self._rmap = {}
            self._npix = len(self._ipix)            
        else:
            self._ipix = None
            self._rmap = None
            self._npix = self._maxpix

        self._ebins = ebins
        if self._ebins is not None:
            self._evals = np.sqrt(self._ebins[0:-1]*self._ebins[1:])
        else:
            self._evals = None
            
        if self._ipix is not None:
            for i,ipixel in enumerate(self._ipix.flat):
                self._rmap[ipixel] = i
                pass


    def __getitem__(self,sliced):
        """ This implements the global-to-local lookup

        sliced:   An array of HEALPix pixel indices

        For all-sky maps it just returns the input array.
        For partial-sky maps in returns the local indices corresponding to the
        indices in the input array, and -1 for those pixels that are outside the 
        selected region.
        """
        
        if self._rmap is not None:
            retval = np.zeros((sliced.size),'i')
            for i,v in enumerate(sliced.flat):
                if self._rmap.has_key(v):
                    retval[i] = self._rmap[v]
                else:
                    retval[i] = -1
            retval = retval.reshape(sliced.shape)
            return retval
        return sliced
            
    @property
    def ordering(self):
        if self._nest: 
            return "NESTED"
        return "RING"

    @property
    def nside(self):
        return self._nside

    @property
    def nest(self):
        return self._nest

    @property
    def npix(self):
        return self._npix

    @property
    def ebins(self):
        return self._ebins

    @property
    def coordsys(self):
        return self._coordsys

    @property
    def evals(self):
        return self._evals    

    @property
    def region(self):
        return self._region


    @staticmethod
    def create_from_header(header,ebins=None):
        """ Creates an HPX object from a FITS header.

        header : The FITS header
        ebins  : Energy bin edges [optional]
        """
        if header["PIXTYPE"] != "HEALPIX":
            raise Expection("PIXTYPE != HEALPIX")
        if  header["ORDERING"] == "RING":
            nest = False
        elif header["ORDERING"] == "NESTED":
            nest = True
        else:
            raise Expection("ORDERING != RING | NESTED")
        order = header["ORDER"]
        if order < 0: 
            nside = header["NSIDE"]
        else:
            nside = -1
        coordsys = header["COORDSYS"]
        try:
            region = header["HPX_REG"]
        except:
            try:
                region = header["HPXREGION"]
            except:
                region = None
        return HPX(nside,nest,coordsys,order,region,ebins=ebins)


    def make_header(self):
        """ Builds and returns FITS header for this HEALPix map """
        cards = [pf.Card("TELESCOP","GLAST"),
                 pf.Card("INSTRUME", "LAT"),
                 pf.Card("COORDSYS",self._coordsys),                 
                 pf.Card("PIXTYPE","HEALPIX"),
                 pf.Card("ORDERING",self.ordering),
                 pf.Card("ORDER",self._order),
                 pf.Card("NSIDE",self._nside),
                 pf.Card("FIRSTPIX",0),
                 pf.Card("LASTPIX",self._maxpix-1)]
        if self._coordsys=="CEL":
            cards.append(pf.Card("EQUINOX", 2000.0,"Equinox of RA & DEC specifications"))
            
        if self._region:
            cards.append(pf.Card("HPX_REG", self._region))
            
        header = pf.Header(cards)
        return header


    def make_hdu(self,data,extname="SKYMAP"):
        """ Builds and returns a FITs HDU with input data

        data      : The data begin stored
        extname   : The HDU extension name        
        """
        shape = data.shape
        if shape[-1] != self._npix:
            raise Exception("Size of data array does not match number of pixels")
        cols = []
        if self._region:
            cols.append(pf.Column("PIX","J",array=self._ipix))
        if len(shape) == 1:
            cols.append(pf.Column("CHANNEL1","D",array=data))
        elif len(shape) == 2:
            for i in range(shape[0]):
                cols.append(pf.Column("CHANNEL%i"%(i+1),"D",array=data[i]))
                pass
        else:
            raise Exception("HPX.write_fits only handles 1D and 2D maps")
        header = self.make_header()
        hdu = pf.BinTableHDU.from_columns(cols,header=header,name=extname)
        return hdu

    
    def make_energy_bounds_hdu(self,extname="EBOUNDS"):
        """ Builds and returns a FITs HDU with the energy bin boundries

        extname   : The HDU extension name            
        """
        if self._ebins is None:
            return None
        cols = [pf.Column("CHANNEL","I",array=np.arange(1,len(self._ebins+1))),
                pf.Column("E_MIN","1E",unit='keV',array=1000*(10**self._ebins[0:-1])),
                pf.Column("E_MAX","1E",unit='keV',array=1000*(10**self._ebins[1:]))]
        hdu = pf.BinTableHDU.from_columns(cols,self.make_header(),name=extname)
        return hdu


    def write_fits(self,data,outfile,extname="SKYMAP",clobber=True):
        """ Write input data to a FITS file

        data      : The data begin stored
        outfile   : The name of the output file
        extname   : The HDU extension name        
        clobber   : True -> overwrite existing files
        """
        hdu_prim = pf.PrimaryHDU()
        hdu_hpx = self.make_hdu(data,extname)
        hl = [hdu_prim,hdu_hpx]
        hdu_ebounds = self.make_energy_bounds_hdu()
        if hdu_ebounds is not None:
            hl.append(hdu_ebounds)
        hdulist = pf.HDUList(hl)
        hdulist.writeto(outfile,clobber=clobber)
        

    @staticmethod
    def get_index_list(nside,nest,region):
        """ Returns the list of pixels indices for all the pixels in a region

        nside    : HEALPix nside parameter
        nest     : True for 'NESTED', False = 'RING'
        region   : HEALPix region string
        """
        tokens = re.split('\(|\)|,',region)
        if tokens[0] == 'DISK':
            vec = coords_to_vec(float(tokens[1]),float(tokens[2]))
            ilist = hp.query_disc(nside,vec[0],np.radians(float(tokens[3])),
                                  inclusive=False,nest=nest)
        elif tokens[0] == 'DISK_INC':
            vec = coords_to_vec(float(tokens[1]),float(tokens[2]))
            ilist = hp.query_disc(nside,vec[0],np.radians(float(tokens[3])),
                                  inclusive=True,fact=int(tokens[4]),
                                  nest=nest)
        elif tokens[0] == 'HPX_PIXEL':
            nside_pix = int(tokens[2])
            if tokens[1] == 'NESTED':
                ipix_ring = hp.nest2ring(nside_pix,int(tokens[3]))
            elif tokens[1] == 'RING':
                ipix_ring = int(tokens[3])
            else:
                raise Exception("Did not recognize ordering scheme %s"%tokens[1])            
            ilist = match_hpx_pixel(nside,nest,nside_pix,ipix_ring)
        else:
            raise Exception("HPX.get_index_list did not recognize region type %s"%tokens[0])
        return ilist

        
    @staticmethod
    def get_ref_dir(region,coordsys):
        """ Finds and returns the reference direction for a given 
        HEALPix region string.   

        region   : a string describing a HEALPix region
        coordsys : coordinate system, GAL | CEL
        """
        if region is None:
            if coordsys == "GAL":
                c = SkyCoord(0.,0.,Galactic, unit="deg")    
            elif coordsys == "CEL":
                c = SkyCoord(0.,0., ICRS, unit="deg")   
            return c
        tokens = re.split('\(|\)|,',region)
        if tokens[0] in  ['DISK','DISK_INC']:
            if coordsys == "GAL":
                c = SkyCoord(float(tokens[1]),float(tokens[2]), Galactic, unit="deg")    
            elif coordsys == "CEL":
                c = SkyCoord(float(tokens[1]),float(tokens[2]), ICRS, unit="deg")   
            return c
        elif tokens[0] == 'HPX_PIXEL':
            nside_pix = int(tokens[2])
            ipix_pix =  int(tokens[3])
            if tokens[1] == 'NESTED':
                nest_pix = True
            elif tokens[1] == 'RING':
                nest_pix = False
            else:
                raise Exception("Did not recognize ordering scheme %s"%tokens[1])
            theta,phi = hp.pix2ang(nside_pix,ipix_pix,nest_pix)
            lat = np.degrees( (np.pi / 2) - theta )
            lon = np.degrees( phi )
            print lon,lat
            if coordsys == "GAL":
                c = SkyCoord(lon,lat, Galactic, unit="deg")    
            elif coordsys == "CEL":
                c = SkyCoord(lon,lat, ICRS, unit="deg")   
            return c
        else:
            raise Exception("HPX.get_ref_dir did not recognize region type %s"%tokens[0])
        return None


    @staticmethod
    def get_region_size(region):
        """ Finds and returns the approximate size of region (in degrees)  
        from a HEALPix region string.   
        """
        if region is None:
            return 180.
        tokens = re.split('\(|\)|,',region)
        if tokens[0] in  ['DISK','DISK_INC']:
            return float(tokens[3])
        elif tokens[0] == 'HPX_PIXEL':
            pixel_size = get_pixel_size_from_nside(int(tokens[2]))
            print 2*pixel_size
            return 2.*pixel_size
        else:
            raise Exception("HPX.get_region_size did not recognize region type %s"%tokens[0])
        return None


    def make_wcs(self,naxis=2,proj='CAR',energies=None,oversample=2):
        """
        """
        from astropy import wcs

        w = wcs.WCS(naxis=naxis)
    
        skydir = self.get_ref_dir(self._region,self.coordsys)

        if self.coordsys == 'CEL':
            w.wcs.ctype[0] = 'RA---%s'%(proj)
            w.wcs.ctype[1] = 'DEC--%s'%(proj)
            w.wcs.crval[0]=skydir.ra.deg
            w.wcs.crval[1]=skydir.dec.deg
        elif self.coordsys == 'GAL':
            w.wcs.ctype[0] = 'GLON-%s'%(proj)
            w.wcs.ctype[1] = 'GLAT-%s'%(proj)
            w.wcs.crval[0]=skydir.galactic.l.deg
            w.wcs.crval[1]=skydir.galactic.b.deg
            print w.wcs.crval[0],w.wcs.crval[1]
        else:
            raise Exception('Unrecognized coordinate system.')
    
        pixsize = get_pixel_size_from_nside(self.nside)
        roisize = min(self.get_region_size(self._region),90)

        npixels = int(2.*roisize/pixsize)*oversample
        crpix = npixels / 2.

        w.wcs.crpix[0] = crpix
        w.wcs.crpix[1] = crpix
        w.wcs.cdelt[0] = -pixsize/oversample
        w.wcs.cdelt[1] = pixsize/oversample
        
        if naxis == 3:
            w.wcs.crpix[2] = 1
            w.wcs.ctype[2] = 'Energy'   
            if energies is not None:
                w.wcs.crval[2] = 10 ** energies[0]
                w.wcs.cdelt[2] = 10 ** energies[1] - 10 ** energies[0]        

        w = wcs.WCS(w.to_header())
        return w



class HpxToWcsMapping(object):
    """ Stores the indices need to conver from HEALPix to WCS """    
    def __init__(self,hpx,wcs):
        """
        """
        self._hpx = hpx
        self._wcs = wcs
        self._ipixs,self._mult_val,self._npix = make_hpx_to_wcs_mapping(self.hpx,self.wcs)
        self._lmap = self._hpx[self._ipixs]
        self._valid = self._lmap > 0        

    @property
    def hpx(self):
        """ The HEALPix projection """
        return self._hpx

    @property
    def wcs(self):
        """ The WCS projection """
        return self._wcs

    @property
    def ipixs(self):
        """ An array(nx,ny) of the global HEALPix pixel indices for each WCS pixel """
        return self._ipixs
    
    @property
    def mult_val(self):
        """ An array(nx,ny) of 1/number of WCS pixels pointing at each HEALPix pixel """
        return self._mult_val

    @property
    def npix(self):
        """ A tuple(nx,ny) of the shape of the WCS grid """
        return self._npix

    @property
    def lmap(self):
        """ An array(nx,ny) giving the mapping of the local HEALPix pixel indices for each WCS pixel """
        return self._lmap
    
    @property
    def valid(self):
        """ An array(nx,ny) of bools giving if each WCS pixel in inside the HEALPix region """
        return self._valid
    
    def fill_wcs_map_from_hpx_data(self,hpx_data,wcs_data,normalize=True):
        """ Fills the wcs map from the hpx data using the pre-calculated mappings 

        hpx_data  : the input HEALPix data
        wcs_data  : the data array being filled
        normalize : True -> perserve integral by splitting HEALPix values between bins
        """

        # FIXME, there really ought to be a better way to do this
        hpx_data_flat = hpx_data.flatten()
        wcs_data_flat = np.zeros((wcs_data.size))
        lmap_valid = self._lmap[self._valid]
        wcs_data_flat[self._valid] = hpx_data_flat[lmap_valid]
        if normalize:
            wcs_data_flat *= self._mult_val
        wcs_data.flat = wcs_data_flat


    
class HpxMap(Map_Base):
    """ Representation of a 2D or 3D counts map using HEALPix. """

    def __init__(self,counts,hpx):
        """ C'tor, fill with a counts vector and a HPX object """
        Map_Base.__init__(self,counts)
        self._hpx = hpx
        self._wcs2d = None
        self._hpx2wcs = None

    @property
    def hpx(self):
        return self._hpx        

    @staticmethod
    def create_from_hdu(hdu,ebins):
        """ Creates and returns an HpxMap object from a FITS HDU.

        hdu    : The FITS 
        ebins  : Energy bin edges [optional]
        """
        hpx = HPX.create_from_header(hdu.header,ebins)        
        colnames = hdu.columns.names
        nebin = 0
        for c in colnames:
            if c.find("CHANNEL") == 0:
                nebin += 1
            pass
        data = np.ndarray((nebin,hpx.npix))
        for i in range(nebin):
            cname = "CHANNEL%i"%(i+1)
            data[i,0:] = hdu.data.field(cname)
            pass
        return HpxMap(data,hpx)


    @staticmethod
    def create_from_hdulist(hdulist,extname="SKYMAP",ebounds="EBOUNDS"):
        """ Creates and returns an HpxMap object from a FITS HDUList

        extname : The name of the HDU with the map data
        ebounds : The name of the HDU with the energy bin data
        """
        if ebounds is not None:
            try:
                ebins = read_energy_bounds(hdulist[ebounds])
            except:
                ebins = None
        else:
            ebins = None

        hpxMap = HpxMap.create_from_hdu(hdulist[extname],ebins)
        
        return hpxMap
   

    def make_wcs_from_hpx(self,sum_ebins=False,proj='CAR',oversample=2,normalize=True):
        """ Make a WCS object and convert HEALPix data into WCS projection

        sum_ebins  : bool, sum energy bins over energy bins before reprojecting
        proj       : WCS-projection
        oversample : Oversampling factor for WCS map
        normalize  : True -> perserve integral by splitting HEALPix values between bins

        returns (WCS object, np.ndarray() with reprojected data)

           NOTE: this re-calculates the mapping, if you have already calculated the 
        mapping it is much faster to use convert_to_cached_wcs() instead
        """
        self._wcs_proj = proj
        self._wcs_oversample = oversample
        self._wcs_2d = self.hpx.make_wcs(2,proj=proj,oversample=oversample)
        self._hpx2wcs = HpxToWcsMapping(self.hpx,self._wcs_2d)
        wcs,wcs_data = self.convert_to_cached_wcs(self.counts,sum_ebins,normalize)
        return wcs,wcs_data
           

    def convert_to_cached_wcs(self,hpx_in,sum_ebins=False,normalize=True):
        """ Make a WCS object and convert HEALPix data into WCS projection

        hpx_in     : HEALPix input data
        sum_ebins  : bool, sum energy bins over energy bins before reprojecting
        normalize  : True -> perserve integral by splitting HEALPix values between bins

        returns (WCS object, np.ndarray() with reprojected data)
        """
        if self._hpx2wcs is None:
            raise Exception("HpxMap.convert_to_cached_wcs() called before make_wcs_from_hpx()")

        if len(hpx_in.shape) == 1:
            wcs_data = np.ndarray(self._hpx2wcs.npix)
            loop_ebins = False
            hpx_data = hpx_in
        elif len(hpx_in.shape) == 2:
            if sum_ebins:
                wcs_data = np.ndarray(self._hpx2wcs.npix)
                hpx_data = hpx_in.sum(1)
                loop_ebins = False
            else:
                wcs_data = np.ndarray((self.counts.shape[0],self._hpx2wcs.npix[0],self._hpx2wcs.npix[1]))
                hpx_data = hpx_in
                loop_ebins = True
        else:
            raise Exception('Wrong dimension for HpxMap %i'%len(hpx_in.shape))
        
        if loop_ebins:
            for i in range(hpx_data.shape[0]):
                self._hpx2wcs.fill_wcs_map_from_hpx_data(hpx_data[i],wcs_data[i],normalize)
                pass
            wcs_data.reshape((self.counts.shape[0],self._hpx2wcs.npix[0],self._hpx2wcs.npix[1]))
            # replace the WCS with a 3D one
            wcs = self.hpx.make_wcs(3,proj=self._wcs_proj,
                                    energies=self.hpx.ebins,oversample=self._wcs_oversample)
        else:
            self._hpx2wcs.fill_wcs_map_from_hpx_data(hpx_data,wcs_data,normalize)
            wcs_data.reshape(self._hpx2wcs.npix)
            wcs = self._wcs_2d 

        return wcs,wcs_data




if __name__ == "__main__":
    
    import numpy as np
    from utils import write_fits_image

    n = np.ones((10,192),'d')
    hpx = HPX(4,False,"GAL")
    hpx.write_fits(n,"test_hpx.fits",clobber=True)

    ebins = np.logspace(2,5,8)
    
    hpx_2 = HPX(1024,False,"GAL",region="DISK(110.,75.,2.)",ebins=ebins)
    #hpx_2 = HPX(1024,False,"GAL",region="HPX_PIXEL(RING,16,500)",ebins=ebins)
    npixels = hpx_2.npix
    print "Npixels",npixels

    n2 = np.ndarray((8,npixels),'d')
    for i in range(8):
        n2[i].flat = np.arange(npixels)
        pass

    hpx_map = HpxMap(n2,hpx_2)
    wcs,wcs_data = hpx_map.make_wcs_from_hpx(normalize=True)

    wcs_out = hpx_2.make_wcs(3)   
    write_fits_image(wcs_data,wcs_out,"test_hpx_2_wcs.fits")
    