# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/models.ipynb (unless otherwise specified).

__all__ = ['AutoARIMA', 'ETS', 'SimpleExponentialSmoothing', 'SimpleExponentialSmoothingOptimized',
           'SeasonalExponentialSmoothing', 'SeasonalExponentialSmoothingOptimized', 'HistoricAverage', 'Naive',
           'RandomWalkWithDrift', 'SeasonalNaive', 'WindowAverage', 'SeasonalWindowAverage', 'ADIDA', 'CrostonClassic',
           'CrostonOptimized', 'CrostonSBA', 'IMAPA', 'TSB']

# Cell
from itertools import count
from numbers import Number
from typing import Collection, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from numba import njit
from scipy.optimize import minimize
from scipy.stats import norm

from .arima import auto_arima_f, forecast_arima, fitted_arima
from .ets import ets_f, forecast_ets

# Internal Cell
class _TS:

    def new(self):
        b = type(self).__new__(type(self))
        b.__dict__.update(self.__dict__)
        return b

# Cell
class AutoARIMA(_TS):

    def __init__(
            self,
            season_length: int = 1, # Number of observations per cycle
            approximation: bool = False, #
        ):
        self.season_length = season_length
        self.approximation = approximation

    def __repr__(self):
        return f'AutoARIMA()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: Optional[np.ndarray] = None, # exogenous regressors
        ):
        with np.errstate(invalid='ignore'):
            self.model_ = auto_arima_f(
                y,
                xreg=X,
                period=self.season_length,
                approximation=self.approximation,
                allowmean=False, allowdrift=False #not implemented yet
            )
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            level: Optional[Tuple[int]] = None, # level
        ):
        fcst = forecast_arima(self.model_, h=h, xreg=X, level=level)
        if level is None:
            return fcst['mean']
        out = [
            fcst['mean'],
            *[fcst['lower'][f'{l}%'] for l in level],
            *[fcst['upper'][f'{l}%'] for l in level],
        ]
        return np.vstack(out).T

    def predict_in_sample(self):
        return fitted_arima(self.model_)

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            level: Optional[Tuple[int]] = None, # level
            fitted: bool = False, # return fitted values?
        ):
        with np.errstate(invalid='ignore'):
            mod = auto_arima_f(
                y,
                xreg=X,
                period=self.season_length,
                approximation=self.approximation,
                allowmean=False, allowdrift=False #not implemented yet
            )
        fcst = forecast_arima(mod, h, xreg=X_future, level=level)
        mean = fcst['mean']
        if fitted:
            return {'mean': mean, 'fitted': fitted_arima(mod)}
        if level is None:
            return {'mean': mean}
        return {
            'mean': mean,
            **{f'lo-{l}': fcst['lower'][f'{l}%'] for l in reversed(level)},
            **{f'hi-{l}': fcst['upper'][f'{l}%'] for l in level},
        }

# Cell
class ETS(_TS):

    def __init__(self, season_length: int = 1, model: str = 'ZZZ'):
        self.season_length = season_length
        self.model = model

    def __repr__(self):
        return f'ETS(sl={self.season_length},model={self.model})'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        self.model_ = ets_f(y, m=self.season_length, model=self.model)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return forecast_ets(self.model_, h=h)['mean']

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        mod = ets_f(y, m=self.season_length, model=self.model)
        fcst = forecast_ets(mod, h)
        keys = ['mean']
        if fitted:
            keys.append('fitted')
        return {key: fcst[key] for key in keys}

# Cell
@njit
def _ses_fcst_mse(x: np.ndarray, alpha: float) -> Tuple[float, float]:
    """Perform simple exponential smoothing on a series.

    This function returns the one step ahead prediction
    as well as the mean squared error of the fit.
    """
    smoothed = x[0]
    n = x.size
    mse = 0.
    fitted = np.full(n, np.nan, np.float32)

    for i in range(1, n):
        smoothed = (alpha * x[i - 1] + (1 - alpha) * smoothed).item()
        error = x[i] - smoothed
        mse += error * error
        fitted[i] = smoothed

    mse /= n
    forecast = alpha * x[-1] + (1 - alpha) * smoothed
    return forecast, mse, fitted


def _ses_mse(alpha: float, x: np.ndarray) -> float:
    """Compute the mean squared error of a simple exponential smoothing fit."""
    _, mse, _ = _ses_fcst_mse(x, alpha)
    return mse


@njit
def _ses_forecast(x: np.ndarray, alpha: float) -> float:
    """One step ahead forecast with simple exponential smoothing."""
    forecast, _, fitted = _ses_fcst_mse(x, alpha)
    return forecast, fitted


@njit
def _demand(x: np.ndarray) -> np.ndarray:
    """Extract the positive elements of a vector."""
    return x[x > 0]


@njit
def _intervals(x: np.ndarray) -> np.ndarray:
    """Compute the intervals between non zero elements of a vector."""
    y = []

    ctr = 1
    for val in x:
        if val == 0:
            ctr += 1
        else:
            y.append(ctr)
            ctr = 1

    y = np.array(y)
    return y


@njit
def _probability(x: np.ndarray) -> np.ndarray:
    """Compute the element probabilities of being non zero."""
    return (x != 0).astype(np.int32)


def _optimized_ses_forecast(
        x: np.ndarray,
        bounds: Sequence[Tuple[float, float]] = [(0.1, 0.3)]
    ) -> float:
    """Searches for the optimal alpha and computes SES one step forecast."""
    alpha = minimize(
        fun=_ses_mse,
        x0=(0,),
        args=(x,),
        bounds=bounds,
        method='L-BFGS-B'
    ).x[0]
    forecast, fitted = _ses_forecast(x, alpha)
    return forecast, fitted


@njit
def _chunk_sums(array: np.ndarray, chunk_size: int) -> np.ndarray:
    """Splits an array into chunks and returns the sum of each chunk."""
    n = array.size
    n_chunks = n // chunk_size
    sums = np.empty(n_chunks)
    for i, start in enumerate(range(0, n, chunk_size)):
        sums[i] = array[start : start + chunk_size].sum()
    return sums

@njit
def _repeat_val(val: float, h: int):
    return np.full(h, val, np.float32)

@njit
def _repeat_val_seas(season_vals: np.ndarray, h: int, season_length: int):
    out = np.empty(h, np.float32)
    for i in range(h):
        out[i] = season_vals[i % season_length]
    return out

# Internal Cell
@njit
def _ses(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
        alpha: float, # smoothing parameter
    ):
    fcst, _, fitted_vals = _ses_fcst_mse(y, alpha)
    mean = _repeat_val(val=fcst, h=h)
    fcst = {'mean': mean}
    if fitted:
        fcst['fitted'] = fitted_vals
    return fcst

# Cell
class SimpleExponentialSmoothing(_TS):

    def __init__(self, alpha: float):
        self.alpha = alpha

    def __repr__(self):
        return f'SES(alpha={self.alpha})'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _ses(y=y, alpha=self.alpha, h=1, fitted=True)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(val=self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _ses(y=y, h=h, fitted=fitted, alpha=self.alpha)
        return out

# Internal Cell
def _ses_optimized(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
    ):
    fcst, fitted_vals = _optimized_ses_forecast(y, [(0.01, 0.99)])
    mean = _repeat_val(val=fcst, h=h)
    fcst = {'mean': mean}
    if fitted:
        fcst['fitted'] = fitted_vals
    return fcst

# Cell
class SimpleExponentialSmoothingOptimized(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'SESOpt()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _ses_optimized(y=y, h=1, fitted=True)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(val=self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _ses_optimized(y=y, h=h, fitted=fitted)
        return out

# Internal Cell
@njit
def _seasonal_exponential_smoothing(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
        season_length: int, # length of season
        alpha: float, # smoothing parameter
    ):
    if y.size < season_length:
        return {'mean': np.full(h, np.nan, np.float32)}
    season_vals = np.empty(season_length, np.float32)
    fitted_vals = np.full(y.size, np.nan, np.float32)
    for i in range(season_length):
        season_vals[i], fitted_vals[i::season_length] = _ses_forecast(y[i::season_length], alpha)
    out = _repeat_val_seas(season_vals=season_vals, h=h, season_length=season_length)
    fcst = {'mean': out}
    if fitted:
        fcst['fitted'] = fitted_vals
    return fcst

# Cell
class SeasonalExponentialSmoothing(_TS):

    def __init__(self, season_length: int, alpha: float):
        self.season_length = season_length
        self.alpha = alpha

    def __repr__(self):
        return f'SeasonalES(sl={self.season_length},alpha={self.alpha})'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _seasonal_exponential_smoothing(
            y=y,
            season_length=self.season_length,
            alpha=self.alpha,
            fitted=True,
            h=self.season_length,
        )
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val_seas(self.model_['mean'], season_length=self.season_length, h=h)

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _seasonal_exponential_smoothing(
            y=y, h=h, fitted=fitted,
            alpha=self.alpha,
            season_length=self.season_length
        )
        return out

# Internal Cell
def _seasonal_ses_optimized(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool , # fitted values
        season_length: int, # season length
    ):
    if y.size < season_length:
        return {'mean': np.full(h, np.nan, np.float32)}
    season_vals = np.empty(season_length, np.float32)
    fitted_vals = np.full(y.size, np.nan, np.float32)
    for i in range(season_length):
        season_vals[i], fitted_vals[i::season_length] = _optimized_ses_forecast(y[i::season_length], [(0.01, 0.99)])
    out = _repeat_val_seas(season_vals=season_vals, h=h, season_length=season_length)
    fcst = {'mean': out}
    if fitted:
        fcst['fitted'] = fitted_vals
    return fcst

# Cell
class SeasonalExponentialSmoothingOptimized(_TS):

    def __init__(self, season_length: int):
        self.season_length = season_length

    def __repr__(self):
        return f'SeasESOpt(sl={self.season_length})'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _seasonal_ses_optimized(
            y=y,
            season_length=self.season_length,
            fitted=True,
            h=self.season_length,
        )
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val_seas(self.model_['mean'], season_length=self.season_length, h=h)

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _seasonal_ses_optimized(
            y=y, h=h, fitted=fitted,
            season_length=self.season_length
        )
        return out

# Internal Cell
@njit
def _historic_average(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
    ):
    mean = _repeat_val(val=y.mean(), h=h)
    fcst = {'mean': mean}
    if fitted:
        fitted_vals = np.full(y.size, np.nan, np.float32)
        fitted_vals[1:] = y.cumsum()[:-1] / np.arange(1, y.size)
        fcst['fitted'] = fitted_vals
    return fcst

# Cell
class HistoricAverage(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'HistoricAverage()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _historic_average(y, h=1, fitted=True)
        self.model_ = dict(mod)
        return self

    def predict(
        self,
        h: int, # forecasting horizon
        X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(val=self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _historic_average(y=y, h=h, fitted=fitted)
        return out

    def prediction_intervals(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            fitted: bool = True, # return fitted values?
            level: Optional[Tuple[int]] = None # confidence level
        ):
        out = _historic_average(y=y, h=h, fitted=fitted)
        steps = np.arange(1,h+1)

        if level is None:
            level = (80,95) # default prediction intervals

        residuals = y-out['fitted']
        sigma = np.sqrt(1/(len(y)-1)*np.nansum(np.power(residuals,2)))
        sigmah = sigma*np.sqrt(1+(1/len(y)))

        z = quantiles(np.asarray(level))
        zz = np.repeat(z,h)
        zz = zz.reshape(z.shape[0],h)

        lower = out['mean']-zz*sigmah
        upper = out['mean']+zz*sigmah

        pred_int = {'lower': lower, 'upper': upper}

        return {'mean': out['mean'],
                'lo': pred_int['lower'],
                'hi': pred_int['upper']
               }

# Internal Cell
@njit
def _naive(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
    ):
    mean = _repeat_val(val=y[-1], h=h)

    if fitted:
        fitted_vals = np.full(y.size, np.nan, np.float32)
        fitted_vals[1:] = np.roll(y, 1)[1:]

        return {'mean': mean, 'fitted': fitted_vals}

    return {'mean': mean}

# Cell
class Naive(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'Naive()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _naive(y, h=1, fitted=True)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _naive(y=y, h=h, fitted=fitted)
        return out

    def prediction_intervals(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            fitted: bool = True, # return fitted values?
            level: Optional[Tuple[int]] = None # confidence level
        ):
        out = _naive(y=y, h=h, fitted=fitted)
        steps = np.arange(1,h+1)

        if level is None:
            level = (80,95) # default prediction intervals

        residuals = y-out['fitted']
        sigma = np.sqrt(1/(len(y)-1)*np.nansum(np.power(residuals,2)))
        sigmah = sigma*np.sqrt(steps)

        z = quantiles(np.asarray(level))
        zz = np.repeat(z,h)
        zz = zz.reshape(z.shape[0],h)

        lower = out['mean']-zz*sigmah
        upper = out['mean']+zz*sigmah

        pred_int = {'lower': lower, 'upper': upper}

        return {'mean': out['mean'],
                'lo': pred_int['lower'],
                'hi': pred_int['upper']
               }

# Internal Cell
@njit
def _random_walk_with_drift(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
    ):
    slope = (y[-1] - y[0]) / (y.size - 1)
    mean = slope * (1 + np.arange(h)) + y[-1]
    fcst = {'mean': mean.astype(np.float32),
            'slope': np.array([slope], dtype=np.float32),
            'last_y': np.array([y[-1]], dtype=np.float32)}
    if fitted:
        fitted_vals = np.full(y.size, np.nan, dtype=np.float32)
        fitted_vals[1:] = (slope + y[:-1]).astype(np.float32)
        fcst['fitted'] = fitted_vals
    return fcst

# Cell
class RandomWalkWithDrift(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'RWD()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _random_walk_with_drift(y, h=1, fitted=True)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        hrange = np.arange(h, dtype=np.float32)
        return self.model_['slope'] * (1 + hrange) + self.model_['last_y']

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _random_walk_with_drift(y=y, h=h, fitted=fitted)
        return out

    def prediction_intervals(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            fitted: bool = True, # return fitted values?
            level: Optional[Tuple[int]] = None # confidence level
        ):
        out = _random_walk_with_drift(y=y, h=h, fitted=fitted)
        steps = np.arange(1,h+1)

        if level is None:
            level = (80,95) # default prediction intervals

        residuals = y-out['fitted']
        sigma = np.sqrt(1/(len(y)-1)*np.nansum(np.power(residuals,2)))
        sigmah = sigma*np.sqrt(steps*(1+steps/(len(y)-1)))

        z = quantiles(np.asarray(level))
        zz = np.repeat(z,h)
        zz = zz.reshape(z.shape[0],h)

        lower = out['mean']-zz*sigmah
        upper = out['mean']+zz*sigmah

        pred_int = {'lower': lower, 'upper': upper}

        return {'mean': out['mean'],
                'lo': pred_int['lower'],
                'hi': pred_int['upper']
               }

# Internal Cell
@njit
def _seasonal_naive(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, #fitted values
        season_length: int, # season length
    ):
    if y.size < season_length:
        return {'mean': np.full(h, np.nan, np.float32)}
    season_vals = np.empty(season_length, np.float32)
    fitted_vals = np.full(y.size, np.nan, np.float32)
    for i in range(season_length):
        s_naive = _naive(y[i::season_length], h=1, fitted=fitted)
        season_vals[i] = s_naive['mean'].item()
        if fitted:
            fitted_vals[i::season_length] = s_naive['fitted']
    out = _repeat_val_seas(season_vals=season_vals, h=h, season_length=season_length)
    fcst = {'mean': out}
    if fitted:
        fcst['fitted'] = fitted_vals
    return fcst

# Cell
class SeasonalNaive(_TS):

    def __init__(self, season_length: int):
        self.season_length = season_length

    def __repr__(self):
        return f'SeasonalNaive(sl={self.season_length})'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _seasonal_naive(
            y=y,
            season_length=self.season_length,
            h=self.season_length,
            fitted=True,
        )
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val_seas(season_vals=self.model_['mean'], season_length=self.season_length, h=h)

    def predict_in_sample(self):
        return self.model_['fitted']

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _seasonal_naive(
            y=y, h=h, fitted=fitted,
            season_length=self.season_length
        )
        return out

    def prediction_intervals(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            fitted: bool = True, # return fitted values?
            level: Optional[Tuple[int]] = None # confidence level
        ):
        out = _seasonal_naive(
            y=y, h=h, fitted=fitted,
            season_length=self.season_length
        )

        k = np.floor((h-1)/self.season_length)

        steps = np.arange(1,h+1)

        if level is None:
            level = (80,95) # default prediction intervals

        residuals = y-out['fitted']
        sigma = np.sqrt(1/(len(y)-self.season_length)*np.nansum(np.power(residuals,2)))
        sigmah = sigma*np.sqrt(k+1)

        z = quantiles(np.asarray(level))
        zz = np.repeat(z,h)
        zz = zz.reshape(z.shape[0],h)

        lower = out['mean']-zz*sigmah
        upper = out['mean']+zz*sigmah

        pred_int = {'lower': lower, 'upper': upper}

        return {'mean': out['mean'],
                'lo': pred_int['lower'],
                'hi': pred_int['upper']
               }

# Internal Cell
@njit
def _window_average(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
        window_size: int, # window size
    ):
    if fitted:
        raise NotImplementedError('return fitted')
    if y.size < window_size:
        return {'mean': np.full(h, np.nan, np.float32)}
    wavg = y[-window_size:].mean()
    mean = _repeat_val(val=wavg, h=h)
    return {'mean': mean}

# Cell
class WindowAverage(_TS):

    def __init__(self, window_size: int):
        self.window_size = window_size

    def __repr__(self):
        return f'WindowAverage(ws={self.window_size})'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _window_average(y=y, h=1, window_size=self.window_size, fitted=False)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        raise NotImplementedError

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _window_average(y=y, h=h, fitted=fitted, window_size=self.window_size)
        return out

# Internal Cell
@njit
def _seasonal_window_average(
        y: np.ndarray,
        h: int,
        fitted: bool,
        season_length: int,
        window_size: int,
    ):
    if fitted:
        raise NotImplementedError('return fitted')
    min_samples = season_length * window_size
    if y.size < min_samples:
        return {'mean': np.full(h, np.nan, np.float32)}
    season_avgs = np.zeros(season_length, np.float32)
    for i, value in enumerate(y[-min_samples:]):
        season = i % season_length
        season_avgs[season] += value / window_size
    out = _repeat_val_seas(season_vals=season_avgs, h=h, season_length=season_length)
    return {'mean': out}

# Cell
class SeasonalWindowAverage(_TS):

    def __init__(self, season_length: int, window_size: int):
        self.season_length = season_length
        self.window_size = window_size

    def __repr__(self):
        return f'SeasWA(sl={self.season_length},ws={self.window_size})'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _seasonal_window_average(
            y=y,
            h=self.season_length,
            fitted=False,
            season_length=self.season_length,
            window_size=self.window_size,
        )
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val_seas(season_vals=self.model_['mean'], season_length=self.season_length, h=h)

    def predict_in_sample(self):
        raise NotImplementedError

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _seasonal_window_average(
            y=y, h=h, fitted=fitted,
            season_length=self.season_length,
            window_size=self.window_size
        )
        return out

# Internal Cell
def _adida(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
    ):
    if fitted:
        raise NotImplementedError('return fitted')
    if (y == 0).all():
        return {'mean': np.repeat(np.float32(0), h)}
    y_intervals = _intervals(y)
    mean_interval = y_intervals.mean()
    aggregation_level = round(mean_interval)
    lost_remainder_data = len(y) % aggregation_level
    y_cut = y[lost_remainder_data:]
    aggregation_sums = _chunk_sums(y_cut, aggregation_level)
    sums_forecast, _ = _optimized_ses_forecast(aggregation_sums)
    forecast = sums_forecast / aggregation_level
    mean = _repeat_val(val=forecast, h=h)
    return {'mean': mean}

# Cell
class ADIDA(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'ADIDA()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _adida(y=y, h=1, fitted=False)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(val=self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        raise NotImplementedError

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _adida(y=y, h=h, fitted=fitted)
        return out

# Internal Cell
@njit
def _croston_classic(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
    ):
    if fitted:
        raise NotImplementedError('return fitted')
    yd = _demand(y)
    yi = _intervals(y)
    ydp, _ = _ses_forecast(yd, 0.1)
    yip, _ = _ses_forecast(yi, 0.1)
    mean = ydp / yip
    mean = _repeat_val(val=mean, h=h)
    return {'mean': mean}

# Cell
class CrostonClassic(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'CrostonClassic()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _croston_classic(y=y, h=1, fitted=False)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(val=self.model_['mean'][0], h=h)

    def predict_in_sample(self, level):
        raise NotImplementedError

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _croston_classic(y=y, h=h, fitted=fitted)
        return out

# Internal Cell
def _croston_optimized(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
    ):
    if fitted:
        raise NotImplementedError('return fitted')
    yd = _demand(y)
    yi = _intervals(y)
    ydp, _ = _optimized_ses_forecast(yd)
    yip, _ = _optimized_ses_forecast(yi)
    mean = ydp / yip
    mean = _repeat_val(val=mean, h=h)
    return {'mean': mean}

# Cell
class CrostonOptimized(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'CrostonSBA()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _croston_optimized(y=y, h=1, fitted=False)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(val=self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        raise NotImplementedError

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _croston_optimized(y=y, h=h, fitted=fitted)
        return out

# Internal Cell
@njit
def _croston_sba(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool,  # fitted values
    ):
    if fitted:
        raise NotImplementedError('return fitted')
    mean = _croston_classic(y, h, fitted)
    mean['mean'] *= 0.95
    return mean

# Cell
class CrostonSBA(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'CrostonSBA()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # # exogenous regressors
        ):
        mod = _croston_sba(y=y, h=1, fitted=False)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(val=self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        raise NotImplementedError

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _croston_sba(y=y, h=h, fitted=fitted)
        return out

# Internal Cell
def _imapa(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: bool, # fitted values
    ):
    if fitted:
        raise NotImplementedError('return fitted')
    if (y == 0).all():
        return {'mean': np.repeat(np.float32(0), h)}
    y_intervals = _intervals(y)
    mean_interval = y_intervals.mean().item()
    max_aggregation_level = round(mean_interval)
    forecasts = np.empty(max_aggregation_level, np.float32)
    for aggregation_level in range(1, max_aggregation_level + 1):
        lost_remainder_data = len(y) % aggregation_level
        y_cut = y[lost_remainder_data:]
        aggregation_sums = _chunk_sums(y_cut, aggregation_level)
        forecast, _ = _optimized_ses_forecast(aggregation_sums)
        forecasts[aggregation_level - 1] = (forecast / aggregation_level)
    forecast = forecasts.mean()
    mean = _repeat_val(val=forecast, h=h)
    return {'mean': mean}

# Cell
class IMAPA(_TS):

    def __init__(self):
        pass

    def __repr__(self):
        return f'IMAPA()'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _imapa(y=y, h=1, fitted=False)
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(val=self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        raise NotImplementedError

    def forecast(
        self,
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        X: np.ndarray = None, # exogenous regressors
        X_future: np.ndarray = None, # future regressors
        fitted: bool = False, # return fitted values?
    ):
        out = _imapa(y=y, h=h, fitted=fitted)
        return out

# Internal Cell
@njit
def _tsb(
        y: np.ndarray, # time series
        h: int, # forecasting horizon
        fitted: int, # fitted values
        alpha_d: float,
        alpha_p: float,
    ):
    if fitted:
        raise NotImplementedError('return fitted')
    if (y == 0).all():
        return {'mean': np.repeat(np.float32(0), h)}
    yd = _demand(y)
    yp = _probability(y)
    ypf, _ = _ses_forecast(yp, alpha_p)
    ydf, _ = _ses_forecast(yd, alpha_d)
    forecast = np.float32(ypf * ydf)
    mean = _repeat_val(val=forecast, h=h)
    return {'mean': mean}

# Cell
class TSB(_TS):

    def __init__(self, alpha_d: float, alpha_p: float):
        self.alpha_d = alpha_d
        self.alpha_p = alpha_p

    def __repr__(self):
        return f'TSB(d={self.alpha_d},p={self.alpha_p})'

    def fit(
            self,
            y: np.ndarray, # time series
            X: np.ndarray = None # exogenous regressors
        ):
        mod = _tsb(
            y=y, h=1,
            fitted=False,
            alpha_d=self.alpha_d,
            alpha_p=self.alpha_p
        )
        self.model_ = dict(mod)
        return self

    def predict(
            self,
            h: int, # forecasting horizon
            X: np.ndarray = None # exogenous regressors
        ):
        return _repeat_val(self.model_['mean'][0], h=h)

    def predict_in_sample(self):
        raise NotImplementedError

    def forecast(
            self,
            y: np.ndarray, # time series
            h: int, # forecasting horizon
            X: np.ndarray = None, # exogenous regressors
            X_future: np.ndarray = None, # future regressors
            fitted: bool = False, # return fitted values?
        ):
        out = _tsb(
            y=y, h=h,
            fitted=fitted,
            alpha_d=self.alpha_d,
            alpha_p=self.alpha_p
        )
        return out