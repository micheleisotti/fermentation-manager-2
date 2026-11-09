"""
Applikon ez2-Control driver — TO BE IMPLEMENTED on the bioreactor PC.

Known so far (from the lab setup):
- two controllers on a local Ethernet switch, reachable from the PC's browser
- each controller serves its own web UI (not a screen mirror); opening one IP shows both
- the web UI is open and does not require a paid licence

Plan:
1. Inspect the web UI traffic (browser dev tools) to find how values are fetched
   (JSON/XHR endpoints, page scraping, or a documented interface such as OPC UA/Modbus
   if the controller offers one — check the manual first).
2. Implement read(): values, setpoints, process limits, base cumulative (dose monitoring),
   pump activation %, pH control mode and PID parameters.
3. Only then implement writes, starting with setpoints, always followed by read-back
   in services/commands.py. Actuator settings stay controller-only.
"""

from __future__ import annotations

from .base import ControllerDriver, ControllerSnapshot, DriverCapabilities, DriverError, PhControl


class Ez2ControlDriver(ControllerDriver):
    capabilities = DriverCapabilities(read_values=False)

    def __init__(self, reactor_id: str, address: str):
        self.reactor_id = reactor_id
        self.address = address

    def read(self) -> ControllerSnapshot:
        raise DriverError("ez2-Control driver not implemented yet")

    def write_setpoint(self, parameter: str, value: float) -> None:
        raise DriverError("ez2-Control driver not implemented yet")

    def write_ph_control(self, control: PhControl) -> None:
        raise DriverError("ez2-Control driver not implemented yet")
