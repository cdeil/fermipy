from __future__ import absolute_import, division, print_function, \
    unicode_literals

import copy
import numpy as np

class SEDFunctor(object):
    """Functor that accepts a model parameter vector and computes the
    normalization of a spectral model in a sequence of SED energy
    bins.
    """
    def __init__(self,spectrum,scale,emin,emax):

        self._emin = emin
        self._emax = emax
        self._scale = scale
        self._spectrum = spectrum

    @property
    def emin(self):
        return self._emin

    @property
    def emax(self):
        return self._emax

    @property
    def scale(self):
        return self._scale
    
class SEDFluxFunctor(SEDFunctor):
    """Functor that computes the flux of a source in a sequence of SED
    energy bins."""
    
    def __init__(self,spectrum,scale,emin,emax):
        super(SEDFluxFunctor,self).__init__(spectrum, scale, emin, emax)
        
    def __call__(self,params):
        return self._spectrum.eval_flux(self._emin,self._emax,
                                        params,self.scale)

class SEDEFluxFunctor(SEDFunctor):
    """Functor that computes the energy flux of a source in a sequence
    of SED energy bins."""
    
    def __init__(self,spectrum,scale,emin,emax):
        super(SEDEFluxFunctor,self).__init__(spectrum, scale, emin, emax)
        
    def __call__(self,params):
        return self._spectrum.eval_eflux(self._emin,self._emax,
                                         params,self.scale)
        
class SpectralFunction(object):
    """Base class for spectral model classes."""
    
    def __init__(self, params, scale = 1.0):
        self._params = params
        self._scale = scale
    
    @property
    def params(self):
        return self._params

    @property
    def scale(self):
        return self._scale

    @staticmethod
    def create_functor(spec_type,func_type,emin,emax,scale=1.0):
        
        if func_type.lower() == 'flux':
            return eval(spec_type).create_flux_functor(emin,emax,scale)
        elif func_type.lower() == 'eflux':
            return eval(spec_type).create_eflux_functor(emin,emax,scale)
        else:
            raise Exception("Did not recognize func_type: %s"%func_type)
        
    @classmethod
    def create_flux_functor(cls,emin,emax,escale=1.0):
        return SEDFluxFunctor(cls,escale,emin,emax)

    @classmethod
    def create_eflux_functor(cls,emin,emax,escale=1.0):
        return SEDEFluxFunctor(cls,escale,emin,emax)

    @classmethod
    def eval_e2dfde(cls, x, params, scale=1.0):
        return cls.eval_dfde(x,params,scale)*x**2

    @classmethod
    def eval_edfde(cls, x, params, scale=1.0):
        return cls.eval_dfde(x,params,scale)*x

    @classmethod
    def integrate(cls, fn, emin, emax, params, scale=1.0, npt=10):
        """Fast numerical integration method using mid-point rule."""
        
        xedges = np.linspace(0.0,1.0,npt+1)        
        logx_edge = np.log(emin) + xedges[:,np.newaxis]*(np.log(emax)-np.log(emin)) 
        logx = 0.5*(logx_edge[1:,...]+logx_edge[:-1,...])
        xw = np.exp(logx_edge[1:,...])-np.exp(logx_edge[:-1,...])
        x = np.exp(logx)
        dfde = fn(x,params,scale)
        return np.sum(dfde*xw,axis=0)
    
    @classmethod
    def eval_flux(cls, emin, emax, params, scale=1.0):
        return cls.integrate(cls.eval_dfde,emin,emax,params,scale)
        
    @classmethod
    def eval_eflux(cls,emin, emax, params, scale=1.0):
        return cls.integrate(cls.eval_edfde,emin,emax,params,scale)
    
    def dfde(self, x):
        """Evaluate differential flux."""
        return self.eval_dfde(x, self.params, self.scale)

    def e2dfde(self, x):
        """Evaluate E^2 times differential flux."""
        return self.eval_dfde(x, self.params, self.scale)*x**2

    def flux(self, emin, emax):
        """Evaluate the integral flux."""
        return self.eval_flux(emin, emax, self.params, self.scale)

    def eflux(self, emin, emax):
        """Evaluate the energy flux flux."""
        return self.eval_eflux(emin, emax, self.params, self.scale)

    
class PowerLaw(SpectralFunction):
    def __init__(self, params, scale=1.0):
        super(PowerLaw,self).__init__(params, scale)

    @staticmethod
    def eval_dfde(x, params, scale=1.0):
        return params[0] * (x / scale) ** params[1]
    
    @classmethod
    def eval_flux(cls, xmin, xmax, params, scale=1.0):
        phi0 = params[0]
        index = params[1]
        x0 = scale
        
        if np.allclose(index, -1.0):
            return phi0 * x0 ** (-index) * (np.log(xmax) - np.log(xmin))

        y0 = x0 * phi0 * (xmin / x0) ** (index + 1) / (index + 1)
        y1 = x0 * phi0 * (xmax / x0) ** (index + 1) / (index + 1)
        v = y1 - y0

        return y1 - y0

    @classmethod
    def eval_eflux(cls,xmin, xmax, params, scale=1.0):

        params = copy.deepcopy(params)
        params[1] += 1.0        
        return cls.eval_flux(xmin,xmax,params,scale)*scale
        
    @staticmethod
    def eval_norm(scale, index, xmin, xmax, flux):
        return flux / PowerLaw.eval_flux(xmin,xmax, [1.0, index], scale)


class LogParabola(SpectralFunction):
    def __init__(self, params, scale=1.0):
        super(LogParabola,self).__init__(params, scale)
    
    @staticmethod
    def eval_dfde(x, params, scale=1.0):
        return params[0] * (x / scale) ** (params[1]-params[2]*np.log(x/scale))

    
class PLExpCutoff(SpectralFunction):
    def __init__(self, params, scale=1.0):
        super(PLExpCutoff,self).__init__(params, scale)
    
    @staticmethod
    def eval_dfde(x, params, scale=1.0):
        return params[0] * (x / scale) ** (params[1]) * np.exp(-x/params[2])