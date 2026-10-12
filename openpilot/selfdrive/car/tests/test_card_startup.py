import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from opendbc.car import structs
from opendbc.car.mock.values import CAR as MOCK
from opendbc.car.volkswagen.values import CAR as VW
from openpilot.selfdrive.car import card


class TestCardStartup(unittest.TestCase):
  """Run the real Car initializer with isolated params and no hardware/socket writes."""

  def setUp(self):
    self.params = Mock()
    self.params.get.return_value = None
    self.params.get_bool.side_effect = lambda key, **kwargs: key in ("OpenpilotEnabledToggle", "IsMetric")
    self.sm = MagicMock()
    self.sm.frame = 0
    self.sm.all_checks.return_value = True
    self.pm = Mock(sock={"sendcan": Mock()})
    self.can_recv, self.can_send = Mock(return_value=[]), Mock()
    self.rk = Mock(remaining=0.0)
    self.cruise = Mock()

    for target, value in (
      ("Params", Mock(return_value=self.params)),
      ("messaging.sub_sock", Mock()),
      ("messaging.SubMaster", Mock(return_value=self.sm)),
      ("messaging.PubMaster", Mock(return_value=self.pm)),
      ("can_comm_callbacks", Mock(return_value=(self.can_recv, self.can_send))),
      ("Ratekeeper", Mock(return_value=self.rk)),
      ("VCruiseHelper", Mock(return_value=self.cruise)),
    ):
      patcher = patch(f"openpilot.selfdrive.car.card.{target}", value)
      patcher.start()
      self.addCleanup(patcher.stop)

  @staticmethod
  def make_interface(platform):
    interface = card.interfaces[platform]
    fingerprint = {bus: {} for bus in range(7)}
    cp = interface.get_params(platform, fingerprint, [], alpha_long=False, is_release=False, docs=False)
    cp_sp = interface.get_params_sp(cp, platform, fingerprint, [], alpha_long=False, is_release_sp=False, docs=False)
    cp_ic = interface.get_params_ic(cp, platform, fingerprint, [], alpha_long=False, is_release_sp=False, docs=False)
    return Mock(CP=cp, CP_SP=cp_sp, CP_IC=cp_ic, CC=Mock())

  def assert_initialized(self, car, interface):
    self.assertIs(car.CI, interface)
    self.assertIs(car.rk, self.rk)
    self.assertIs(car.v_cruise_helper, self.cruise)
    keys = {args[0] for args, _ in self.params.put.call_args_list}
    self.assertTrue({"CarParams", "CarParamsSP", "CarParamsIC"}.issubset(keys))
    interface.pre_init.assert_called_once()
    self.can_send.assert_not_called()
    self.pm.send.assert_not_called()

  def test_init_with_supplied_interface(self):
    for platform in (VW.VOLKSWAGEN_CADDY_MK5, VW.VOLKSWAGEN_GOLF_MK7, MOCK.MOCK):
      with self.subTest(platform=platform):
        interface = self.make_interface(platform)
        car = card.Car(CI=interface, RI=Mock())
        self.assert_initialized(car, interface)

  def test_init_after_fingerprint(self):
    for platform in (VW.VOLKSWAGEN_CADDY_MK5, VW.VOLKSWAGEN_GOLF_MK7, MOCK.MOCK):
      with self.subTest(platform=platform):
        interface = self.make_interface(platform)
        radar = Mock()
        radar_factory = Mock(return_value=radar)
        can = card.messaging.new_message("can", 1)
        can.can[0].address = 0x123
        with patch.object(card.messaging, "recv_one_retry", return_value=can), \
             patch.object(card, "get_car", return_value=interface) as get_car, \
             patch.object(card.sunnypilot_interfaces, "setup_interfaces") as setup, \
             patch.dict(card.interfaces, {platform: SimpleNamespace(RadarInterface=radar_factory)}, clear=True):
          car = card.Car()
        self.assert_initialized(car, interface)
        self.assertIs(car.RI, radar)
        get_car.assert_called_once()
        setup.assert_called_once_with(interface, self.params)
        radar_factory.assert_called_once_with(interface.CP, interface.CP_SP)
        self.params.put_bool.assert_any_call("FirmwareQueryDone", True, block=True)

  def test_disabled_toggle_keeps_no_output_safety(self):
    self.params.get_bool.side_effect = lambda key, **kwargs: key == "IsMetric"
    interface = self.make_interface(VW.VOLKSWAGEN_CADDY_MK5)
    car = card.Car(CI=interface, RI=Mock())
    self.assert_initialized(car, interface)
    self.assertTrue(car.CP.passive)
    self.assertEqual(len(car.CP.safetyConfigs), 1)
    self.assertEqual(car.CP.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)

  def test_publish_after_startup(self):
    for can_valid in (True, False):
      with self.subTest(can_valid=can_valid):
        self.pm.send.reset_mock()
        car = card.Car(CI=self.make_interface(VW.VOLKSWAGEN_CADDY_MK5), RI=Mock())
        cs = structs.CarState.new_message(canValid=can_valid)
        cs_sp = card.convert_to_capnp(structs.CarStateSP())
        cs_ic = card.convert_to_capnp(structs.CarStateIC())
        for frame in range(100):
          self.sm.frame = frame
          car.state_publish(cs, cs_sp, cs_ic, None)
        counts = Counter(args[0] for args, _ in self.pm.send.call_args_list)
        for service in ("carState", "carOutput", "carStateSP", "carStateIC"):
          self.assertEqual(counts[service], 100)
        for service in ("carParams", "carParamsSP", "carParamsIC"):
          self.assertEqual(counts[service], 1)
        self.assertEqual(counts["sendcan"], 0)
        for args, _ in self.pm.send.call_args_list:
          if args[0] in ("carState", "carStateSP", "carStateIC"):
            self.assertEqual(args[1].valid, can_valid)
        self.can_send.assert_not_called()


if __name__ == "__main__":
  unittest.main()
