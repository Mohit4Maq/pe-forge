"""pwrlab -- power-electronics design, simulation and verification.

The working assumption of this package is that a simulation result is a
hypothesis until it has been checked against an independent closed-form model
and shown to be insensitive to the solver's numerical settings.
"""
from .sim import Runner, SimResult, default_command
from .checks import Check, CheckSet, ERROR, WARN, INFO
from .waveforms import Waves
from .study import Study, StudyResult

__all__ = ["Runner", "SimResult", "default_command", "Check", "CheckSet",
           "Waves", "Study", "StudyResult", "ERROR", "WARN", "INFO"]
