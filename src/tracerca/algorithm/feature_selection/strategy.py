from abc import ABC, abstractmethod

import numpy as np


class FeatureSelectionStrategy(ABC):
    """Determines whether a feature's empirical distribution is anomalous vs. reference."""

    @abstractmethod
    def is_anomalous(self, empirical: np.ndarray, reference: np.ndarray) -> bool: 
        ...


class DistributionCriteria(FeatureSelectionStrategy):
    """
    3-sigma outlier ratio comparison.
    Flags a feature when the fraction of empirical values outside 3 standard deviations
    of the reference mean exceeds the reference's own outlier fraction by more than
    `threshold` times that fraction.
    """

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold

    def is_anomalous(self, empirical: np.ndarray, reference: np.ndarray) -> bool:
        empirical, reference = np.array(empirical), np.array(reference)
        historical_mean, historical_std = np.mean(reference), np.std(reference)
        ref_ratio = sum(np.abs(reference - historical_mean) > 3 * historical_std) / reference.shape[0]
        emp_ratio = sum(np.abs(empirical - historical_mean) > 3 * historical_std) / empirical.shape[0]
        return (emp_ratio - ref_ratio) > self.threshold * ref_ratio


class FisherCriteria(FeatureSelectionStrategy):
    """
    Fisher score: squared mean difference divided by pooled variance.
    Flags a feature when the score exceeds `threshold`.
    `side` controls whether the mean shift is measured bidirectionally ('two-sided'),
    or only when empirical mean is greater ('less') / smaller ('greater') than reference.
    """

    def __init__(self, threshold: float = 1.0, side: str = "two-sided") -> None:
        if side not in ("two-sided", "less", "greater"):
            raise ValueError(f"invalid side: {side!r}")
        self.threshold = threshold
        self.side = side

    def is_anomalous(self, empirical: np.ndarray, reference: np.ndarray) -> bool:
        if self.side == "two-sided":
            diff_mean = (np.abs(np.mean(empirical) - np.mean(reference)) ** 2)
        elif self.side == "less":
            diff_mean = np.maximum(np.mean(empirical) - np.mean(reference), 0) ** 2
        else:  # greater
            diff_mean = np.maximum(np.mean(reference) - np.mean(empirical), 0) ** 2
        variance = np.maximum(np.var(empirical) + np.var(reference), 0.1)
        return (diff_mean / variance) > self.threshold


class StderrCriteria(FeatureSelectionStrategy):
    """
    Mean absolute deviation ratio comparison, normalized by reference std.
    Flags a feature when the empirical MAD/std ratio exceeds the reference ratio
    by more than `threshold` times that ratio plus a fixed margin of 1.0.
    A floor is applied to std to avoid division by near-zero values.
    """

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold

    def is_anomalous(self, empirical: np.ndarray, reference: np.ndarray) -> bool:
        empirical, reference = np.array(empirical), np.array(reference)
        historical_mean, historical_std = np.mean(reference), np.std(reference)
        historical_std = np.maximum(historical_std, historical_mean * 0.01 + 0.01)
        ref_ratio = np.mean(np.abs(reference - historical_mean)) / historical_std
        emp_ratio = np.mean(np.abs(empirical - historical_mean)) / historical_std
        return (emp_ratio - ref_ratio) > self.threshold * ref_ratio + 1.0
