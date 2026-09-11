from __future__ import annotations

from getpass import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from fractions import Fraction
from typing import Any, Iterable

from PySide6.QtCore import QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import (
	QCamera,
	QCameraDevice,
	QCameraFormat,
	QImageCapture,
	QMediaCaptureSession,
	QMediaFormat,
	QMediaDevices,
	QMediaRecorder,
)
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
	QApplication,
	QCheckBox,
	QComboBox,
	QDoubleSpinBox,
	QFileDialog,
	QFormLayout,
	QGridLayout,
	QGroupBox,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QMainWindow,
	QMessageBox,
	QPushButton,
	QScrollArea,
	QSlider,
	QSplitter,
	QSpinBox,
	QTableWidget,
	QTableWidgetItem,
	QTextEdit,
	QVBoxLayout,
	QWidget,
)


class ClickableVideoWidget(QVideoWidget):
	clicked_normalized = Signal(float, float)

	def mousePressEvent(self, event) -> None:
		if event.button() == Qt.LeftButton and self.width() > 0 and self.height() > 0:
			nx = event.position().x() / float(self.width())
			ny = event.position().y() / float(self.height())
			nx = max(0.0, min(1.0, nx))
			ny = max(0.0, min(1.0, ny))
			self.clicked_normalized.emit(nx, ny)
		super().mousePressEvent(event)


class CameraTestWindow(QMainWindow):
	def __init__(self) -> None:
		super().__init__()
		self.setWindowTitle("Stockwood Camera Test Lab")
		self.resize(1600, 980)

		self.media_devices = QMediaDevices()
		self.capture_session = QMediaCaptureSession()
		self.camera: QCamera | None = None
		self.image_capture = QImageCapture()
		self.recorder = QMediaRecorder()

		self.capture_session.setImageCapture(self.image_capture)
		self.capture_session.setRecorder(self.recorder)

		self.frame_count = 0
		self.last_frame_time: float | None = None
		self.fps_value = 0.0
		self.last_frame_size = "n/a"
		self.last_frame_ts = "n/a"
		self.shot_counter = 0
		self.rpi_model = self._detect_rpi_model()
		self.exiftool_path = self._detect_exiftool_path()
		self.recording_start_monotonic: float | None = None
		self.pending_record_path: str | None = None
		self.pending_record_attempts = 0
		self.last_record_path: str | None = None

		self._build_ui()
		if self.exiftool_path:
			self.log(f"ExifTool detected: {self.exiftool_path}")
		else:
			self.log("ExifTool not found. Install exiftool to embed still image metadata")
		self._connect_global_signals()
		self.refresh_devices()

		if self.device_combo.count() > 0:
			self.device_combo.setCurrentIndex(0)
			self.on_device_changed(0)

	def _build_ui(self) -> None:
		root = QWidget(self)
		self.setCentralWidget(root)
		root_layout = QHBoxLayout(root)

		splitter = QSplitter(Qt.Horizontal, root)
		root_layout.addWidget(splitter)

		panel_widget = QWidget()
		panel_layout = QVBoxLayout(panel_widget)
		panel_layout.setContentsMargins(8, 8, 8, 8)
		panel_layout.setSpacing(8)

		panel_layout.addWidget(self._build_device_group())
		panel_layout.addWidget(self._build_camera_controls_group())
		panel_layout.addWidget(self._build_capture_group())
		panel_layout.addWidget(self._build_diagnostics_group())
		panel_layout.addStretch(1)

		scroll = QScrollArea()
		scroll.setWidgetResizable(True)
		scroll.setWidget(panel_widget)
		splitter.addWidget(scroll)

		right_side = QWidget()
		right_layout = QVBoxLayout(right_side)
		right_layout.setContentsMargins(8, 8, 8, 8)
		right_layout.setSpacing(8)

		self.video_widget = ClickableVideoWidget()
		self.video_widget.setMinimumSize(860, 540)
		right_layout.addWidget(self.video_widget, stretch=5)
		self.capture_session.setVideoOutput(self.video_widget)

		self.preview_stats = QLabel("Frames: 0 | FPS: 0.0 | Last frame: n/a | Timestamp: n/a")
		right_layout.addWidget(self.preview_stats)

		self.log_output = QTextEdit()
		self.log_output.setReadOnly(True)
		self.log_output.setPlaceholderText("Diagnostics log")
		right_layout.addWidget(self.log_output, stretch=2)

		splitter.addWidget(right_side)
		splitter.setSizes([540, 1060])

	def _build_device_group(self) -> QWidget:
		group = QGroupBox("Device + Format")
		layout = QVBoxLayout(group)

		row0 = QHBoxLayout()
		self.device_combo = QComboBox()
		self.refresh_btn = QPushButton("Refresh")
		row0.addWidget(self.device_combo, stretch=1)
		row0.addWidget(self.refresh_btn)
		layout.addLayout(row0)

		row1 = QHBoxLayout()
		self.start_btn = QPushButton("Start")
		self.stop_btn = QPushButton("Stop")
		self.apply_format_btn = QPushButton("Apply Format")
		row1.addWidget(self.start_btn)
		row1.addWidget(self.stop_btn)
		row1.addWidget(self.apply_format_btn)
		layout.addLayout(row1)

		self.format_combo = QComboBox()
		layout.addWidget(self.format_combo)

		self.device_info = QTextEdit()
		self.device_info.setReadOnly(True)
		self.device_info.setMinimumHeight(120)
		layout.addWidget(self.device_info)
		return group

	def _build_camera_controls_group(self) -> QWidget:
		group = QGroupBox("Camera Controls")
		layout = QVBoxLayout(group)

		form = QFormLayout()
		self.focus_mode_combo = QComboBox()
		self.exposure_mode_combo = QComboBox()
		self.white_balance_combo = QComboBox()
		form.addRow("Focus mode", self.focus_mode_combo)
		form.addRow("Exposure mode", self.exposure_mode_combo)
		form.addRow("White balance", self.white_balance_combo)
		layout.addLayout(form)

		grid = QGridLayout()

		self.zoom_slider = QSlider(Qt.Horizontal)
		self.zoom_slider.setRange(10, 100)
		self.zoom_slider.setValue(10)
		self.zoom_label = QLabel("1.0x")

		self.focus_distance_slider = QSlider(Qt.Horizontal)
		self.focus_distance_slider.setRange(0, 1000)
		self.focus_distance_slider.setValue(0)
		self.focus_distance_label = QLabel("0.000")

		self.exp_comp_slider = QSlider(Qt.Horizontal)
		self.exp_comp_slider.setRange(-100, 100)
		self.exp_comp_slider.setValue(0)
		self.exp_comp_label = QLabel("0.0")

		self.color_temp_slider = QSlider(Qt.Horizontal)
		self.color_temp_slider.setRange(2000, 9000)
		self.color_temp_slider.setValue(6500)
		self.color_temp_label = QLabel("6500K")

		grid.addWidget(QLabel("Zoom"), 0, 0)
		grid.addWidget(self.zoom_slider, 0, 1)
		grid.addWidget(self.zoom_label, 0, 2)

		grid.addWidget(QLabel("Focus distance"), 1, 0)
		grid.addWidget(self.focus_distance_slider, 1, 1)
		grid.addWidget(self.focus_distance_label, 1, 2)

		grid.addWidget(QLabel("Exposure compensation"), 2, 0)
		grid.addWidget(self.exp_comp_slider, 2, 1)
		grid.addWidget(self.exp_comp_label, 2, 2)

		grid.addWidget(QLabel("Color temperature"), 3, 0)
		grid.addWidget(self.color_temp_slider, 3, 1)
		grid.addWidget(self.color_temp_label, 3, 2)

		layout.addLayout(grid)

		manual_row = QHBoxLayout()
		self.iso_spin = QSpinBox()
		self.iso_spin.setRange(50, 12800)
		self.iso_spin.setValue(100)
		self.exposure_time_ms_spin = QDoubleSpinBox()
		self.exposure_time_ms_spin.setRange(0.1, 1000.0)
		self.exposure_time_ms_spin.setDecimals(2)
		self.exposure_time_ms_spin.setValue(8.0)
		self.apply_manual_btn = QPushButton("Apply Manual ISO/Exposure")

		manual_row.addWidget(QLabel("ISO"))
		manual_row.addWidget(self.iso_spin)
		manual_row.addWidget(QLabel("Exposure ms"))
		manual_row.addWidget(self.exposure_time_ms_spin)
		manual_row.addWidget(self.apply_manual_btn)
		layout.addLayout(manual_row)

		self.click_focus_checkbox = QCheckBox("Enable click-to-focus point on preview")
		layout.addWidget(self.click_focus_checkbox)
		return group

	def _build_capture_group(self) -> QWidget:
		group = QGroupBox("Still Capture + Recording")
		layout = QVBoxLayout(group)

		shot_row = QHBoxLayout()
		self.shot_path_edit = QLineEdit(self._default_shot_path())
		self.shot_browse_btn = QPushButton("Browse")
		self.capture_image_btn = QPushButton("Capture Still")
		shot_row.addWidget(self.shot_path_edit, stretch=1)
		shot_row.addWidget(self.shot_browse_btn)
		shot_row.addWidget(self.capture_image_btn)
		layout.addLayout(shot_row)

		rec_row = QHBoxLayout()
		self.record_path_edit = QLineEdit(self._default_record_path())
		self.record_browse_btn = QPushButton("Browse")
		self.record_start_btn = QPushButton("Record")
		self.record_pause_btn = QPushButton("Pause")
		self.record_stop_btn = QPushButton("Stop")
		self.record_pause_btn.setEnabled(False)
		self.record_stop_btn.setEnabled(False)
		rec_row.addWidget(self.record_path_edit, stretch=1)
		rec_row.addWidget(self.record_browse_btn)
		rec_row.addWidget(self.record_start_btn)
		rec_row.addWidget(self.record_pause_btn)
		rec_row.addWidget(self.record_stop_btn)
		layout.addLayout(rec_row)

		self.record_state_label = QLabel("Recorder: idle")
		layout.addWidget(self.record_state_label)
		return group

	def _build_diagnostics_group(self) -> QWidget:
		group = QGroupBox("Diagnostics + Capability Probe")
		layout = QVBoxLayout(group)

		self.prop_table = QTableWidget(0, 2)
		self.prop_table.setHorizontalHeaderLabels(["Property", "Value"])
		self.prop_table.horizontalHeader().setStretchLastSection(True)
		self.prop_table.setMinimumHeight(220)
		layout.addWidget(self.prop_table)

		btn_row = QHBoxLayout()
		self.refresh_props_btn = QPushButton("Refresh Properties")
		self.cap_probe_btn = QPushButton("Run Capability Probe")
		self.export_probe_btn = QPushButton("Export Probe JSON")
		btn_row.addWidget(self.refresh_props_btn)
		btn_row.addWidget(self.cap_probe_btn)
		btn_row.addWidget(self.export_probe_btn)
		layout.addLayout(btn_row)
		return group

	def _connect_global_signals(self) -> None:
		self.refresh_btn.clicked.connect(self.refresh_devices)
		self.device_combo.currentIndexChanged.connect(self.on_device_changed)
		self.start_btn.clicked.connect(self.start_camera)
		self.stop_btn.clicked.connect(self.stop_camera)
		self.apply_format_btn.clicked.connect(self.apply_selected_format)

		self.focus_mode_combo.currentIndexChanged.connect(self.on_focus_mode_changed)
		self.exposure_mode_combo.currentIndexChanged.connect(self.on_exposure_mode_changed)
		self.white_balance_combo.currentIndexChanged.connect(self.on_white_balance_changed)

		self.zoom_slider.valueChanged.connect(self.on_zoom_changed)
		self.focus_distance_slider.valueChanged.connect(self.on_focus_distance_changed)
		self.exp_comp_slider.valueChanged.connect(self.on_exposure_comp_changed)
		self.color_temp_slider.valueChanged.connect(self.on_color_temp_changed)
		self.apply_manual_btn.clicked.connect(self.apply_manual_exposure)

		self.video_widget.clicked_normalized.connect(self.on_preview_clicked)

		self.shot_browse_btn.clicked.connect(self.browse_shot_path)
		self.capture_image_btn.clicked.connect(self.capture_still)
		self.record_browse_btn.clicked.connect(self.browse_record_path)
		self.record_start_btn.clicked.connect(self.start_recording)
		self.record_pause_btn.clicked.connect(self.pause_recording)
		self.record_stop_btn.clicked.connect(self.stop_recording)

		self.refresh_props_btn.clicked.connect(self.refresh_property_table)
		self.cap_probe_btn.clicked.connect(self.run_capability_probe)
		self.export_probe_btn.clicked.connect(self.export_probe_json)

		if hasattr(self.media_devices, "videoInputsChanged"):
			self.media_devices.videoInputsChanged.connect(self.refresh_devices)

		if hasattr(self.image_capture, "imageSaved"):
			self.image_capture.imageSaved.connect(self.on_image_saved)
		if hasattr(self.image_capture, "errorOccurred"):
			self.image_capture.errorOccurred.connect(
				lambda _id, _err, msg: self.log(f"Image capture error: {msg}")
			)
		if hasattr(self.image_capture, "readyForCaptureChanged"):
			self.image_capture.readyForCaptureChanged.connect(
				lambda ready: self.log(f"Image capture ready: {ready}")
			)

		if hasattr(self.recorder, "durationChanged"):
			self.recorder.durationChanged.connect(self.on_recorder_duration_changed)
		if hasattr(self.recorder, "recorderStateChanged"):
			self.recorder.recorderStateChanged.connect(self.on_recorder_state_changed)
		if hasattr(self.recorder, "errorOccurred"):
			self.recorder.errorOccurred.connect(
				lambda _err, msg: self.log(f"Recorder error: {msg}")
			)

		sink = self.video_widget.videoSink() if hasattr(self.video_widget, "videoSink") else None
		if sink and hasattr(sink, "videoFrameChanged"):
			sink.videoFrameChanged.connect(self.on_video_frame)
		else:
			self.log("Preview sink not available; frame telemetry disabled")

		self.live_timer = QTimer(self)
		self.live_timer.setInterval(250)
		self.live_timer.timeout.connect(self.update_preview_stats)
		self.live_timer.start()
		self._update_record_button_states()

	def refresh_devices(self) -> None:
		previous = None
		if self.device_combo.currentIndex() >= 0:
			previous = self.device_combo.currentData()
			previous = self._device_id_str(previous) if previous else None

		self.device_combo.blockSignals(True)
		self.device_combo.clear()

		devices = self.media_devices.videoInputs()
		for dev in devices:
			self.device_combo.addItem(dev.description(), dev)

		self.device_combo.blockSignals(False)

		if self.device_combo.count() == 0:
			self.device_info.setPlainText("No video input devices found")
			self.log("No video input devices found")
			self._set_camera(None)
			return

		if previous:
			for i in range(self.device_combo.count()):
				dev = self.device_combo.itemData(i)
				if self._device_id_str(dev) == previous:
					self.device_combo.setCurrentIndex(i)
					break

		if self.device_combo.currentIndex() < 0:
			self.device_combo.setCurrentIndex(0)

	def on_device_changed(self, _index: int) -> None:
		dev = self.device_combo.currentData()
		if not dev:
			return
		self.log(f"Switching to device: {dev.description()}")
		self._set_camera(dev)

	def _set_camera(self, device: QCameraDevice | None) -> None:
		if self.camera:
			try:
				self.camera.stop()
			except Exception:
				pass
			self.capture_session.setCamera(None)
			self.camera.deleteLater()
			self.camera = None

		if device is None:
			return

		self.camera = QCamera(device)
		self.capture_session.setCamera(self.camera)
		self._connect_camera_signals(self.camera)
		self._populate_device_info(device)
		self._populate_format_combo(device)
		self._populate_enum_controls()
		self._sync_numeric_controls_from_camera()
		self.refresh_property_table()

	def _connect_camera_signals(self, cam: QCamera) -> None:
		for signal_name in (
			"activeChanged",
			"errorChanged",
			"cameraDeviceChanged",
			"cameraFormatChanged",
			"focusModeChanged",
			"exposureModeChanged",
			"whiteBalanceModeChanged",
		):
			sig = getattr(cam, signal_name, None)
			if sig and hasattr(sig, "connect"):
				sig.connect(lambda *args, n=signal_name: self.log(f"Camera signal {n}: {args}"))

		if hasattr(cam, "errorOccurred"):
			cam.errorOccurred.connect(lambda _err, msg: self.log(f"Camera error: {msg}"))

	def _populate_device_info(self, device: QCameraDevice) -> None:
		info = {
			"description": device.description(),
			"id": self._device_id_str(device),
			"is_default": self._device_id_str(device)
			== self._device_id_str(self.media_devices.defaultVideoInput()),
			"formats": len(device.videoFormats()),
		}
		self.device_info.setPlainText(json.dumps(info, indent=2))

	def _populate_format_combo(self, device: QCameraDevice) -> None:
		self.format_combo.clear()
		formats = device.videoFormats()
		for fmt in formats:
			self.format_combo.addItem(self._format_label(fmt), fmt)
		self.log(f"Device reports {len(formats)} camera formats")

	def _populate_enum_controls(self) -> None:
		if not self.camera:
			return

		self._setup_enum_combo(
			combo=self.focus_mode_combo,
			enum_type=getattr(QCamera, "FocusMode", None),
			getter_name="focusMode",
		)
		self._setup_enum_combo(
			combo=self.exposure_mode_combo,
			enum_type=getattr(QCamera, "ExposureMode", None),
			getter_name="exposureMode",
		)
		self._setup_enum_combo(
			combo=self.white_balance_combo,
			enum_type=getattr(QCamera, "WhiteBalanceMode", None),
			getter_name="whiteBalanceMode",
		)

	def _sync_numeric_controls_from_camera(self) -> None:
		if not self.camera:
			return

		min_zoom = self._safe_getter(self.camera, "minimumZoomFactor", default=1.0)
		max_zoom = self._safe_getter(self.camera, "maximumZoomFactor", default=8.0)
		zoom = self._safe_getter(self.camera, "zoomFactor", default=min_zoom)

		min_int = int(float(min_zoom) * 10.0)
		max_int = int(float(max_zoom) * 10.0)
		if max_int <= min_int:
			min_int, max_int = 10, 100
		self.zoom_slider.blockSignals(True)
		self.zoom_slider.setRange(min_int, max_int)
		self.zoom_slider.setValue(int(float(zoom) * 10.0))
		self.zoom_slider.blockSignals(False)
		self.zoom_label.setText(f"{zoom:.2f}x")

		focus_dist = self._safe_getter(self.camera, "focusDistance", default=0.0)
		self.focus_distance_slider.blockSignals(True)
		self.focus_distance_slider.setValue(int(float(focus_dist) * 1000.0))
		self.focus_distance_slider.blockSignals(False)
		self.focus_distance_label.setText(f"{float(focus_dist):.3f}")

		exp_comp = self._safe_getter(self.camera, "exposureCompensation", default=0.0)
		self.exp_comp_slider.blockSignals(True)
		self.exp_comp_slider.setValue(int(float(exp_comp) * 10.0))
		self.exp_comp_slider.blockSignals(False)
		self.exp_comp_label.setText(f"{float(exp_comp):.1f}")

		color_temp = self._safe_getter(self.camera, "colorTemperature", default=6500)
		self.color_temp_slider.blockSignals(True)
		self.color_temp_slider.setValue(int(float(color_temp)))
		self.color_temp_slider.blockSignals(False)
		self.color_temp_label.setText(f"{int(float(color_temp))}K")

	def _setup_enum_combo(self, combo: QComboBox, enum_type: Any, getter_name: str) -> None:
		combo.blockSignals(True)
		combo.clear()
		if enum_type is None or not self.camera:
			combo.setEnabled(False)
			combo.blockSignals(False)
			return

		current_value = self._safe_getter(self.camera, getter_name)
		members = self._enum_members(enum_type)
		for name, value in members:
			combo.addItem(name, value)

		combo.setEnabled(bool(members))
		if current_value is not None:
			idx = combo.findData(current_value)
			if idx >= 0:
				combo.setCurrentIndex(idx)
		combo.blockSignals(False)

	def start_camera(self) -> None:
		if not self.camera:
			self.log("No camera selected")
			return
		try:
			self.camera.start()
			self.log("Camera started")
		except Exception as exc:
			self.log(f"Failed to start camera: {exc}")

	def stop_camera(self) -> None:
		if not self.camera:
			return
		try:
			self.camera.stop()
			self.log("Camera stopped")
		except Exception as exc:
			self.log(f"Failed to stop camera: {exc}")

	def apply_selected_format(self) -> None:
		if not self.camera:
			return
		fmt = self.format_combo.currentData()
		if not isinstance(fmt, QCameraFormat):
			self.log("No format selected")
			return

		if hasattr(self.camera, "setCameraFormat"):
			try:
				self.camera.setCameraFormat(fmt)
				self.log(f"Applied format: {self._format_label(fmt)}")
			except Exception as exc:
				self.log(f"Failed to apply format: {exc}")

	def on_focus_mode_changed(self, _index: int) -> None:
		value = self.focus_mode_combo.currentData()
		self._safe_call_camera("setFocusMode", value)

	def on_exposure_mode_changed(self, _index: int) -> None:
		value = self.exposure_mode_combo.currentData()
		self._safe_call_camera("setExposureMode", value)

	def on_white_balance_changed(self, _index: int) -> None:
		value = self.white_balance_combo.currentData()
		self._safe_call_camera("setWhiteBalanceMode", value)

	def on_zoom_changed(self, value: int) -> None:
		zoom = float(value) / 10.0
		self.zoom_label.setText(f"{zoom:.2f}x")
		self._safe_call_camera("setZoomFactor", zoom)

	def on_focus_distance_changed(self, value: int) -> None:
		dist = float(value) / 1000.0
		self.focus_distance_label.setText(f"{dist:.3f}")
		self._safe_call_camera("setFocusDistance", dist)

	def on_exposure_comp_changed(self, value: int) -> None:
		comp = float(value) / 10.0
		self.exp_comp_label.setText(f"{comp:.1f}")
		self._safe_call_camera("setExposureCompensation", comp)

	def on_color_temp_changed(self, value: int) -> None:
		self.color_temp_label.setText(f"{value}K")
		self._safe_call_camera("setColorTemperature", int(value))

	def apply_manual_exposure(self) -> None:
		iso = int(self.iso_spin.value())
		exposure_ms = float(self.exposure_time_ms_spin.value())

		ok_iso = self._safe_call_camera("setManualIsoSensitivity", iso)
		ok_exp = self._safe_call_camera("setManualExposureTime", exposure_ms / 1000.0)

		self.log(
			f"Manual controls: ISO={iso} ({'ok' if ok_iso else 'unsupported'}), "
			f"Exposure={exposure_ms:.2f}ms ({'ok' if ok_exp else 'unsupported'})"
		)

	def on_preview_clicked(self, nx: float, ny: float) -> None:
		if not self.click_focus_checkbox.isChecked():
			return
		if not self.camera:
			return
		ok = self._safe_call_camera("setCustomFocusPoint", QPointF(nx, ny))
		self.log(
			f"Preview click focus point -> ({nx:.3f}, {ny:.3f}) "
			f"({'applied' if ok else 'unsupported'})"
		)

	def browse_shot_path(self) -> None:
		path, _ = QFileDialog.getSaveFileName(
			self,
			"Choose still image file",
			self.shot_path_edit.text(),
			"Images (*.jpg *.jpeg *.png)",
		)
		if path:
			self.shot_path_edit.setText(path)

	def capture_still(self) -> None:
		path = self.shot_path_edit.text().strip()
		if not path:
			self.log("Still image path is empty")
			return
		self._ensure_parent_dir(path)

		if hasattr(self.image_capture, "captureToFile"):
			try:
				cap_id = self.image_capture.captureToFile(path)
				self.log(f"Requested still capture: id={cap_id}, path={path}")
			except Exception as exc:
				self.log(f"Still capture failed: {exc}")

	def on_image_saved(self, capture_id: int, path: str) -> None:
		self.shot_counter += 1
		self.log(f"Still image saved: id={capture_id}, path={path}")
		self._write_shot_exif(path, capture_id, self.shot_counter)

	def browse_record_path(self) -> None:
		path, _ = QFileDialog.getSaveFileName(
			self,
			"Choose recording file",
			self.record_path_edit.text(),
			"Video (*.mkv *.mp4 *.avi)",
		)
		if path:
			self.record_path_edit.setText(path)

	def start_recording(self) -> None:
		path = self.record_path_edit.text().strip()
		if not path:
			self.log("Recording path is empty")
			return
		if not self.camera:
			self.log("No camera selected")
			return
		path = self._normalize_record_path_for_backend(path)

		state_name = self._current_recorder_state_name().lower()
		if "recording" in state_name:
			self.log("Recorder is already recording")
			return
		if "paused" in state_name:
			try:
				self.recorder.record()
				self.log("Recording resumed")
			except Exception as exc:
				self.log(f"Failed to resume recording: {exc}")
			return

		self._ensure_parent_dir(path)
		self.pending_record_path = path
		self.pending_record_attempts = 25
		self.record_state_label.setText("Recorder: preparing")
		self._update_record_button_states()

		if self.camera and self._safe_getter(self.camera, "isActive", default=False) is False:
			try:
				self.camera.start()
				self.log("Camera started for recording warmup")
			except Exception as exc:
				self.log(f"Failed to start camera for recording: {exc}")

		self._attempt_pending_record_start()

	def pause_recording(self) -> None:
		if self.pending_record_path:
			self.log("Recorder is preparing, pause is unavailable")
			return

		state_name = self._current_recorder_state_name().lower()
		try:
			if "recording" in state_name:
				self.recorder.pause()
				self.log("Recording paused")
			elif "paused" in state_name:
				self.recorder.record()
				self.log("Recording resumed")
			else:
				self.log("Pause ignored: recorder is not active")
		except Exception as exc:
			self.log(f"Failed to toggle pause: {exc}")

	def stop_recording(self) -> None:
		if self.pending_record_path:
			self.pending_record_path = None
			self.pending_record_attempts = 0
			self.recording_start_monotonic = None
			self.record_state_label.setText("Recorder: idle")
			self._update_record_button_states()
			self.log("Canceled pending recording request")
			try:
				self.recorder.stop()
			except Exception:
				pass
			return

		try:
			self.recorder.stop()
			self.recording_start_monotonic = None
			self.log("Recording stopped")
			QTimer.singleShot(450, self._ensure_recorder_stopped)
			QTimer.singleShot(600, self._post_stop_record_file_check)
		except Exception as exc:
			self.log(f"Failed to stop recording: {exc}")

	def on_recorder_duration_changed(self, duration_ms: int) -> None:
		state_name = self._current_recorder_state_name()
		self.record_state_label.setText(f"Recorder: {state_name} | {duration_ms} ms")

	def on_recorder_state_changed(self, state: Any) -> None:
		enum_type = getattr(QMediaRecorder, "RecorderState", type(state))
		state_name = self._enum_name(enum_type, state)
		self.record_state_label.setText(f"Recorder: {state_name}")
		state_name_lower = state_name.lower()
		if "stopped" in state_name_lower:
			self.recording_start_monotonic = None
		if "recording" in state_name_lower:
			self.pending_record_path = None
			self.pending_record_attempts = 0
		self._update_record_button_states()
		self.log(f"Recorder state changed: {state_name}")

	def _current_recorder_state_name(self) -> str:
		state = self._safe_getter(self.recorder, "recorderState")
		enum_type = getattr(QMediaRecorder, "RecorderState", type(state))
		return self._enum_name(enum_type, state)

	def _camera_ready_for_recording(self) -> bool:
		if not self.camera:
			return False
		if self._safe_getter(self.camera, "isActive", default=False) is False:
			return False
		if self.last_frame_time is None:
			return False
		return (time.monotonic() - self.last_frame_time) <= 1.5

	def _attempt_pending_record_start(self) -> None:
		path = self.pending_record_path
		if not path:
			return

		if self._camera_ready_for_recording():
			self.pending_record_path = None
			self.pending_record_attempts = 0
			self._start_recording_now(path)
			return

		if self.pending_record_attempts <= 0:
			self.pending_record_path = None
			self.log("Camera warmup timed out, attempting recording anyway")
			self._start_recording_now(path)
			return

		self.pending_record_attempts -= 1
		QTimer.singleShot(120, self._attempt_pending_record_start)

	def _start_recording_now(self, path: str) -> None:
		try:
			self.last_record_path = path
			if not self._configure_recorder_for_path(path):
				self.log("Recorder is using backend default format selection")
			self.recorder.setOutputLocation(QUrl.fromLocalFile(path))
			self.recorder.record()
			self.recording_start_monotonic = time.monotonic()
			self.log(f"Recording started -> {path}")
		except Exception as exc:
			self.recording_start_monotonic = None
			self.log(f"Failed to start recording: {exc}")
		finally:
			self._update_record_button_states()

	def _update_record_button_states(self) -> None:
		state_name = self._current_recorder_state_name().lower()
		is_preparing = self.pending_record_path is not None
		is_recording = "recording" in state_name
		is_paused = "paused" in state_name
		has_active_attempt = self.recording_start_monotonic is not None

		self.record_start_btn.setEnabled(not is_preparing and not is_recording)
		self.record_start_btn.setText("Resume" if is_paused else "Record")
		self.record_pause_btn.setEnabled(is_recording or is_paused)
		self.record_stop_btn.setEnabled(is_preparing or is_recording or is_paused or has_active_attempt)

	def _ensure_recorder_stopped(self) -> None:
		state_name = self._current_recorder_state_name().lower()
		if "recording" in state_name or "paused" in state_name:
			try:
				self.recorder.stop()
				self.log("Recorder did not stop on first attempt, issued a second stop")
			except Exception as exc:
				self.log(f"Second stop attempt failed: {exc}")
		self._update_record_button_states()

	def _post_stop_record_file_check(self) -> None:
		path = self.last_record_path
		if not path:
			return
		if not os.path.exists(path):
			self.log("Recording file was not created")
			return
		try:
			size = os.path.getsize(path)
		except Exception:
			return
		if size <= 64:
			self.log("Recording output is very small; backend may not support requested container/codec")
			root, ext = os.path.splitext(path)
			if ext.lower() in (".mp4", ".m4v"):
				fallback_path = root + ".mkv"
				self.record_path_edit.setText(fallback_path)
				self.log(f"Switched next recording path to MKV fallback: {fallback_path}")

	def on_video_frame(self, frame: Any) -> None:
		self.frame_count += 1
		now = time.monotonic()
		if self.last_frame_time is not None:
			dt = now - self.last_frame_time
			if dt > 0:
				inst_fps = 1.0 / dt
				if self.fps_value == 0:
					self.fps_value = inst_fps
				else:
					self.fps_value = (self.fps_value * 0.9) + (inst_fps * 0.1)
		self.last_frame_time = now

		try:
			size = frame.size()
			self.last_frame_size = f"{size.width()}x{size.height()}"
		except Exception:
			self.last_frame_size = "n/a"

		try:
			self.last_frame_ts = str(frame.startTime())
		except Exception:
			self.last_frame_ts = "n/a"

	def update_preview_stats(self) -> None:
		self.preview_stats.setText(
			f"Frames: {self.frame_count} | FPS: {self.fps_value:.2f} | "
			f"Last frame: {self.last_frame_size} | Timestamp: {self.last_frame_ts}"
		)

	def refresh_property_table(self) -> None:
		rows: list[tuple[str, str]] = []
		if self.camera:
			mo = self.camera.metaObject()
			for i in range(mo.propertyOffset(), mo.propertyCount()):
				prop = mo.property(i)
				name = prop.name()
				value = self.camera.property(name)
				rows.append((name, self._stringify(value)))

			getters = [
				"isAvailable",
				"isActive",
				"minimumZoomFactor",
				"maximumZoomFactor",
				"zoomFactor",
				"focusDistance",
				"exposureCompensation",
				"isoSensitivity",
				"exposureTime",
				"colorTemperature",
			]
			for getter in getters:
				val = self._safe_getter(self.camera, getter)
				if val is not None:
					rows.append((getter + "()", self._stringify(val)))

		self.prop_table.setRowCount(len(rows))
		for row_idx, (name, value) in enumerate(rows):
			self.prop_table.setItem(row_idx, 0, QTableWidgetItem(name))
			self.prop_table.setItem(row_idx, 1, QTableWidgetItem(value))

		self.log(f"Property table refreshed ({len(rows)} items)")

	def run_capability_probe(self) -> None:
		if not self.camera:
			self.log("Capability probe skipped: no active camera")
			return

		probe = self._build_probe_payload()
		self.log("Capability probe completed")
		self.log(json.dumps(probe, indent=2))

	def export_probe_json(self) -> None:
		if not self.camera:
			self.log("Cannot export probe: no active camera")
			return

		payload = self._build_probe_payload()
		default_name = f"camera_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
		path, _ = QFileDialog.getSaveFileName(
			self,
			"Export capability probe",
			default_name,
			"JSON (*.json)",
		)
		if not path:
			return

		try:
			with open(path, "w", encoding="utf-8") as f:
				json.dump(payload, f, indent=2)
			self.log(f"Probe exported to {path}")
		except Exception as exc:
			self.log(f"Failed to export probe: {exc}")

	def _build_probe_payload(self) -> dict[str, Any]:
		if not self.camera:
			return {}

		device = self.camera.cameraDevice()
		methods_to_check = [
			"start",
			"stop",
			"setCameraFormat",
			"setFocusMode",
			"setFocusDistance",
			"setCustomFocusPoint",
			"setZoomFactor",
			"setExposureMode",
			"setExposureCompensation",
			"setManualIsoSensitivity",
			"setManualExposureTime",
			"setWhiteBalanceMode",
			"setColorTemperature",
		]

		supported_methods = {
			name: hasattr(self.camera, name) and callable(getattr(self.camera, name, None))
			for name in methods_to_check
		}

		enums = {
			"FocusMode": [name for name, _ in self._enum_members(getattr(QCamera, "FocusMode", None))],
			"ExposureMode": [
				name for name, _ in self._enum_members(getattr(QCamera, "ExposureMode", None))
			],
			"WhiteBalanceMode": [
				name for name, _ in self._enum_members(getattr(QCamera, "WhiteBalanceMode", None))
			],
		}

		current_format = self.camera.cameraFormat() if hasattr(self.camera, "cameraFormat") else None

		payload = {
			"timestamp": datetime.now().isoformat(timespec="seconds"),
			"device": {
				"description": device.description(),
				"id": self._device_id_str(device),
				"formats_available": len(device.videoFormats()),
			},
			"camera_state": {
				"active": self._safe_getter(self.camera, "isActive"),
				"available": self._safe_getter(self.camera, "isAvailable"),
				"error": self._stringify(self._safe_getter(self.camera, "error")),
				"error_string": self._safe_getter(self.camera, "errorString"),
			},
			"current_format": self._format_dict(current_format) if current_format else None,
			"frames": {
				"count": self.frame_count,
				"fps_estimate": round(self.fps_value, 3),
				"last_size": self.last_frame_size,
				"last_timestamp": self.last_frame_ts,
			},
			"supported_methods": supported_methods,
			"enum_options": enums,
			"qt_properties": self._collect_qt_properties(self.camera),
		}

		if hasattr(self.camera, "supportedFeatures"):
			try:
				payload["supported_features"] = self._stringify(self.camera.supportedFeatures())
			except Exception:
				payload["supported_features"] = "unavailable"

		return payload

	def _configure_recorder_for_path(self, path: str) -> bool:
		if not hasattr(self.recorder, "setMediaFormat"):
			return True

		if hasattr(self.recorder, "setQuality"):
			quality = getattr(QMediaRecorder, "NormalQuality", None)
			if quality is not None:
				try:
					self.recorder.setQuality(quality)
				except Exception:
					pass

		if hasattr(self.recorder, "setEncodingMode"):
			encoding_mode = self._pick_enum_value(
				getattr(QMediaRecorder, "EncodingMode", None),
				{},
				default_candidates=["ConstantQualityEncoding", "AverageBitRateEncoding"],
			)
			if encoding_mode is not None:
				try:
					self.recorder.setEncodingMode(encoding_mode)
				except Exception:
					pass

		camera_format = self.camera.cameraFormat() if self.camera and hasattr(self.camera, "cameraFormat") else None
		if camera_format is not None:
			resolution = camera_format.resolution()
			if hasattr(self.recorder, "setVideoResolution"):
				try:
					self.recorder.setVideoResolution(resolution)
				except TypeError:
					try:
						self.recorder.setVideoResolution(resolution.width(), resolution.height())
					except Exception:
						pass
				except Exception:
					pass

			if hasattr(self.recorder, "setVideoFrameRate"):
				try:
					max_fps = float(camera_format.maxFrameRate())
					target_fps = min(max(max_fps, 1.0), 30.0) if max_fps > 0 else 30.0
					self.recorder.setVideoFrameRate(target_fps)
				except Exception:
					pass

		target_bitrate = self._recommended_video_bitrate(camera_format)

		if hasattr(self.recorder, "setVideoBitRate"):
			try:
				self.recorder.setVideoBitRate(target_bitrate)
			except Exception:
				pass

		media_format = QMediaFormat()
		extension = os.path.splitext(path)[1].lower()
		encode_mode = self._media_format_encode_mode()
		supported_file_formats = self._supported_media_values("supportedFileFormats", encode_mode)
		supported_video_codecs = self._supported_media_values("supportedVideoCodecs", encode_mode)

		file_format = self._pick_enum_value(
			getattr(QMediaFormat, "FileFormat", None),
			{
				".mp4": ["MPEG4", "MP4"],
				".m4v": ["MPEG4", "MP4"],
				".mkv": ["Matroska"],
				".avi": ["AVI"],
			},
			default_candidates=[],
			key=extension,
			allowed_values=supported_file_formats if supported_file_formats else None,
		)
		has_explicit_media_choice = False
		if file_format is not None and hasattr(media_format, "setFileFormat"):
			try:
				media_format.setFileFormat(file_format)
				has_explicit_media_choice = True
			except Exception:
				pass

		video_codec = self._pick_enum_value(
			getattr(QMediaFormat, "VideoCodec", None),
			{
				".avi": ["MPEG4", "H264"],
			},
			default_candidates=["H264", "AVC", "MPEG4"],
			key=extension,
			allowed_values=supported_video_codecs if supported_video_codecs else None,
		)
		if video_codec is not None and hasattr(media_format, "setVideoCodec") and has_explicit_media_choice:
			try:
				media_format.setVideoCodec(video_codec)
				has_explicit_media_choice = True
			except Exception:
				pass

		is_supported = True
		if has_explicit_media_choice:
			is_supported = self._is_media_format_supported(media_format, encode_mode)

		if has_explicit_media_choice and not is_supported and hasattr(media_format, "setVideoCodec"):
			fallback_codec = self._pick_enum_value(
				getattr(QMediaFormat, "VideoCodec", None),
				{},
				default_candidates=["Unspecified", "Default"],
				allowed_values=supported_video_codecs if supported_video_codecs else None,
			)
			if fallback_codec is not None:
				try:
					media_format.setVideoCodec(fallback_codec)
				except Exception:
					pass
			is_supported = self._is_media_format_supported(media_format, encode_mode)

		if has_explicit_media_choice and is_supported:
			self.recorder.setMediaFormat(media_format)
		elif has_explicit_media_choice and not is_supported:
			self.log("Requested container/codec combo is unsupported; using backend defaults")
			return False

		self.log(
			"Recorder configured for "
			f"{extension or '.mp4'}: file_format={self._stringify(file_format)}, "
			f"video_codec={self._stringify(video_codec)}, bitrate={target_bitrate}, "
			f"supported={is_supported}"
		)
		return True

	def _normalize_record_path_for_backend(self, path: str) -> str:
		extension = os.path.splitext(path)[1].lower()
		if extension not in (".mp4", ".m4v", ".mkv", ".avi"):
			return path

		encode_mode = self._media_format_encode_mode()
		supported_file_formats = self._supported_media_values("supportedFileFormats", encode_mode)
		if not supported_file_formats:
			return path

		requested_format = self._pick_enum_value(
			getattr(QMediaFormat, "FileFormat", None),
			{
				".mp4": ["MPEG4", "MP4"],
				".m4v": ["MPEG4", "MP4"],
				".mkv": ["Matroska"],
				".avi": ["AVI"],
			},
			default_candidates=[],
			key=extension,
		)
		if requested_format is not None and requested_format in supported_file_formats:
			return path

		for target_ext, names in (
			(".mkv", ["Matroska"]),
			(".mp4", ["MPEG4", "MP4"]),
			(".avi", ["AVI"]),
		):
			candidate_format = self._pick_enum_value(
				getattr(QMediaFormat, "FileFormat", None),
				{},
				default_candidates=names,
			)
			if candidate_format is not None and candidate_format in supported_file_formats:
				new_path = os.path.splitext(path)[0] + target_ext
				if new_path != path:
					self.record_path_edit.setText(new_path)
					self.log(
						f"Container {extension} is unsupported here, switched recording path to {target_ext}"
					)
				return new_path

		self.log(f"Container {extension} appears unsupported; continuing with backend defaults")
		return path

	def _recommended_video_bitrate(self, camera_format: QCameraFormat | None) -> int:
		if camera_format is None:
			return 2_500_000

		try:
			resolution = camera_format.resolution()
			pixels = int(resolution.width()) * int(resolution.height())
		except Exception:
			return 2_500_000

		if pixels <= 640 * 480:
			return 1_200_000
		if pixels <= 1280 * 720:
			return 2_500_000
		if pixels <= 1920 * 1080:
			return 4_500_000
		return 6_000_000

	def _media_format_encode_mode(self) -> Any:
		mode = self._pick_enum_value(
			getattr(QMediaFormat, "ConversionMode", None),
			{},
			default_candidates=["Encode"],
		)
		if mode is not None:
			return mode
		return getattr(QMediaFormat, "Encode", None)

	def _supported_media_values(self, method_name: str, mode: Any) -> set[Any]:
		method = getattr(QMediaFormat, method_name, None)
		if not callable(method):
			return set()

		try:
			values = method(mode) if mode is not None else method()
		except TypeError:
			try:
				values = method()
			except Exception:
				return set()
		except Exception:
			return set()

		try:
			return set(values)
		except Exception:
			return set()

	def _is_media_format_supported(self, media_format: QMediaFormat, mode: Any) -> bool:
		checker = getattr(media_format, "isSupported", None)
		if not callable(checker):
			return True

		try:
			return bool(checker(mode)) if mode is not None else bool(checker())
		except TypeError:
			try:
				return bool(checker())
			except Exception:
				return True
		except Exception:
			return True

	def _pick_enum_value(
		self,
		type_obj: Any,
		per_extension_candidates: dict[str, list[str]],
		default_candidates: list[str],
		key: str | None = None,
		allowed_values: set[Any] | None = None,
	) -> Any:
		if type_obj is None:
			return None

		members = dict(self._enum_members(type_obj))
		candidate_names = per_extension_candidates.get(key or "", []) + default_candidates
		for candidate_name in candidate_names:
			if candidate_name in members:
				candidate = members[candidate_name]
				if allowed_values and candidate not in allowed_values:
					continue
				return candidate
		return None

	def _collect_qt_properties(self, obj: Any) -> dict[str, str]:
		props: dict[str, str] = {}
		mo = obj.metaObject()
		for i in range(mo.propertyOffset(), mo.propertyCount()):
			prop = mo.property(i)
			name = prop.name()
			props[name] = self._stringify(obj.property(name))
		return props

	def _safe_call_camera(self, method_name: str, *args: Any) -> bool:
		if not self.camera:
			return False
		method = getattr(self.camera, method_name, None)
		if not callable(method):
			return False
		try:
			method(*args)
			return True
		except Exception as exc:
			self.log(f"{method_name} failed: {exc}")
			return False

	def _safe_getter(self, obj: Any, method_name: str, default: Any = None) -> Any:
		method = getattr(obj, method_name, None)
		if not callable(method):
			return default
		try:
			return method()
		except Exception:
			return default

	def _enum_members(self, enum_type: Any) -> list[tuple[str, Any]]:
		if enum_type is None:
			return []
		members: list[tuple[str, Any]] = []
		for name in dir(enum_type):
			if name.startswith("_"):
				continue
			value = getattr(enum_type, name)
			if callable(value):
				continue
			members.append((name, value))
		return members

	def _enum_name(self, enum_type: Any, value: Any) -> str:
		if enum_type is None:
			return self._stringify(value)
		for name in dir(enum_type):
			if name.startswith("_"):
				continue
			try:
				candidate = getattr(enum_type, name)
				if candidate == value:
					return name
			except Exception:
				pass
		return self._stringify(value)

	def _format_label(self, fmt: QCameraFormat) -> str:
		return (
			f"{fmt.resolution().width()}x{fmt.resolution().height()} | "
			f"{fmt.minFrameRate():.2f}-{fmt.maxFrameRate():.2f} fps | "
			f"pixel={self._stringify(fmt.pixelFormat())}"
		)

	def _format_dict(self, fmt: QCameraFormat) -> dict[str, Any]:
		return {
			"resolution": {
				"width": fmt.resolution().width(),
				"height": fmt.resolution().height(),
			},
			"min_fps": fmt.minFrameRate(),
			"max_fps": fmt.maxFrameRate(),
			"pixel_format": self._stringify(fmt.pixelFormat()),
		}

	def _device_id_str(self, device: QCameraDevice | None) -> str:
		if device is None:
			return ""
		try:
			raw = bytes(device.id())
			return raw.decode("utf-8", "ignore")
		except Exception:
			return self._stringify(device.id())

	def _default_shot_path(self) -> str:
		name = f"still_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
		return os.path.join(os.path.expanduser("~"), "Pictures", name)

	def _default_record_path(self) -> str:
		name = f"record_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mkv"
		return os.path.join(os.path.expanduser("~"), "Videos", name)

	def _detect_rpi_model(self) -> str:
		model_paths = [
			"/proc/device-tree/model",
			"/sys/firmware/devicetree/base/model",
		]
		for model_path in model_paths:
			try:
				with open(model_path, "rb") as f:
					model = f.read().replace(b"\x00", b"").decode("utf-8", "ignore").strip()
					if model:
						return model
			except Exception:
				pass

		try:
			with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
				for line in f:
					if line.startswith("Model") and ":" in line:
						return line.split(":", 1)[1].strip()
		except Exception:
			pass

		return "Unknown Raspberry Pi model"

	def _detect_exiftool_path(self) -> str | None:
		path = shutil.which("exiftool")
		if path:
			return path

		common_paths = [
			"/usr/bin/exiftool",
			"/usr/local/bin/exiftool",
		]
		for candidate in common_paths:
			if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
				return candidate

		return None

	def _build_shot_metadata(self, capture_id: int, shot_number: int) -> dict[str, Any]:
		camera_device = self.camera.cameraDevice() if self.camera else None
		camera_format = self.camera.cameraFormat() if self.camera and hasattr(self.camera, "cameraFormat") else None

		camera_info: dict[str, Any] = {
			"sensor_name": camera_device.description() if camera_device else "unknown",
			"id": self._device_id_str(camera_device) if camera_device else "",
			"format": self._format_dict(camera_format) if camera_format else None,
		}

		exposure_time_s = self._safe_getter(self.camera, "exposureTime", default=None) if self.camera else None
		iso_value = self._safe_getter(self.camera, "isoSensitivity", default=None) if self.camera else None
		zoom_value = self._safe_getter(self.camera, "zoomFactor", default=None) if self.camera else None
		focus_distance_value = self._safe_getter(self.camera, "focusDistance", default=None) if self.camera else None

		now = datetime.now()
		shot_info: dict[str, Any] = {
			"capture_id": capture_id,
			"shot_number": shot_number,
			"captured_at": now.isoformat(timespec="seconds"),
			"captured_at_exif": now.strftime("%Y:%m:%d %H:%M:%S"),
			"iso": int(iso_value) if isinstance(iso_value, (int, float)) else None,
			"exposure_time_s": float(exposure_time_s)
			if isinstance(exposure_time_s, (int, float))
			else None,
			"zoom_factor": float(zoom_value) if isinstance(zoom_value, (int, float)) else None,
			"focus_distance_m": float(focus_distance_value)
			if isinstance(focus_distance_value, (int, float))
			else None,
			"focus_mode": self.focus_mode_combo.currentText(),
			"exposure_mode": self.exposure_mode_combo.currentText(),
			"white_balance_mode": self.white_balance_combo.currentText(),
			"preview": {
				"fps_estimate": round(self.fps_value, 3),
				"last_frame_size": self.last_frame_size,
				"last_frame_timestamp": self.last_frame_ts,
			},
		}

		return {
			"camera": camera_info,
			"rpi_model": self.rpi_model,
			"shot": shot_info,
		}

	def _write_shot_exif(self, path: str, capture_id: int, shot_number: int) -> None:
		ext = os.path.splitext(path)[1].lower()
		if ext not in (".jpg", ".jpeg"):
			self.log("EXIF write skipped: only JPEG supports EXIF in this tool")
			return

		if not self.exiftool_path:
			self.log("EXIF write skipped: exiftool is not available")
			return

		metadata = self._build_shot_metadata(capture_id, shot_number)
		shot_data = metadata.get("shot", {})
		sensor_name = str(metadata.get("camera", {}).get("sensor_name", "unknown"))
		sensor_id = str(metadata.get("camera", {}).get("id", ""))

		iso_value = shot_data.get("iso")
		exposure_s = shot_data.get("exposure_time_s")
		zoom_factor = shot_data.get("zoom_factor")
		focus_distance_m = shot_data.get("focus_distance_m")
		focus_mode = str(shot_data.get("focus_mode") or "unknown")
		exposure_mode = str(shot_data.get("exposure_mode") or "unknown")
		white_balance_mode = str(shot_data.get("white_balance_mode") or "unknown")
		fps_estimate = shot_data.get("preview", {}).get("fps_estimate")
		last_frame_size = str(shot_data.get("preview", {}).get("last_frame_size") or "unknown")

		if isinstance(iso_value, int) and iso_value > 0:
			iso_text = str(iso_value)
		else:
			iso_text = "unknown"

		if isinstance(exposure_s, (int, float)) and exposure_s > 0:
			exposure_text = f"{float(exposure_s):.6f}s"
		else:
			exposure_text = "unknown"

		if isinstance(zoom_factor, (int, float)) and zoom_factor > 0:
			zoom_text = f"{float(zoom_factor):.2f}x"
		else:
			zoom_text = "unknown"

		if isinstance(fps_estimate, (int, float)):
			fps_text = f"{float(fps_estimate):.2f}"
		else:
			fps_text = "unknown"

		captured_at_exif = str(shot_data.get("captured_at_exif") or datetime.now().strftime("%Y:%m:%d %H:%M:%S"))

		exposure_ratio = None
		if isinstance(exposure_s, (int, float)) and exposure_s > 0:
			frac = Fraction(float(exposure_s)).limit_denominator(1000000)
			exposure_ratio = f"{int(frac.numerator)}/{int(frac.denominator)}"

		zoom_ratio = None
		if isinstance(zoom_factor, (int, float)) and zoom_factor > 0:
			frac = Fraction(float(zoom_factor)).limit_denominator(1000)
			zoom_ratio = f"{int(frac.numerator)}/{int(frac.denominator)}"

		exposure_mode_lower = exposure_mode.lower()
		if "auto" in exposure_mode_lower:
			exif_exposure_mode = "0"
		elif "bracket" in exposure_mode_lower:
			exif_exposure_mode = "2"
		else:
			exif_exposure_mode = "1"

		if "auto" in white_balance_mode.lower():
			exif_white_balance = "0"
		else:
			exif_white_balance = "1"

		tag_values: list[tuple[str, str]] = [
			("EXIF:Make", "Raspberry Pi"),
			("EXIF:Model", str(metadata.get("rpi_model", ""))[:255]),
			("EXIF:Artist", "Stub"),
			("EXIF:DateTimeOriginal", captured_at_exif),
			("EXIF:CreateDate", captured_at_exif),
			("EXIF:ModifyDate", captured_at_exif),
				("EXIF:LensModel", sensor_name[:255]),
				("EXIF:LensMake", sensor_name.strip().split()[0]),
			("EXIF:ExposureMode", exif_exposure_mode),
			("EXIF:WhiteBalance", exif_white_balance),
			("EXIF:BodySerialNumber", sensor_id[:255]),
			("EXIF:LensSerialNumber", sensor_id[:255]),
			("XMP:Instructions", f"focus={focus_mode};exp={exposure_mode};wb={white_balance_mode};fps={fps_text};frame={last_frame_size}"),
		]

		if isinstance(iso_value, int) and iso_value > 0:
			tag_values.append(("EXIF:ISO", str(iso_value)))
		if exposure_ratio:
			tag_values.append(("EXIF:ExposureTime", exposure_ratio))
		if zoom_ratio:
			tag_values.append(("EXIF:DigitalZoomRatio", zoom_ratio))
		if isinstance(focus_distance_m, (int, float)) and float(focus_distance_m) >= 0.0:
			tag_values.append(("EXIF:SubjectDistance", f"{float(focus_distance_m):.3f}"))

		# Always clear UserComment to prevent JSON-like payloads from being stored there.
		cmd = [self.exiftool_path, "-overwrite_original", "-P", "-EXIF:UserComment="]
		for tag_name, tag_value in tag_values:
			if tag_value:
				cmd.append(f"-{tag_name}={tag_value}")
		cmd.append(path)

		try:
			result = subprocess.run(cmd, capture_output=True, text=True, check=False)
			if result.returncode != 0:
				err = (result.stderr or result.stdout or "unknown exiftool error").strip()
				self.log(f"ExifTool write failed: {err}")
				return

			verify_cmd = [
				self.exiftool_path,
				"-s3",
				"-EXIF:Make",
				"-EXIF:Model",
				"-EXIF:LensModel",
				"-EXIF:ISO",
				"-EXIF:ExposureTime",
				"-EXIF:WhiteBalance",
				"-EXIF:ExposureMode",
				"-EXIF:ImageNumber",
				"-EXIF:ImageDescription",
				"-EXIF:UserComment",
				path,
			]
			verify_result = subprocess.run(verify_cmd, capture_output=True, text=True, check=False)
			if verify_result.returncode != 0:
				self.log("ExifTool wrote metadata, but verification read failed")
				return

			lines = [line.strip() for line in verify_result.stdout.splitlines()]
			verify_make = lines[0] if len(lines) > 0 else ""
			verify_model = lines[1] if len(lines) > 1 else ""
			verify_lens = lines[2] if len(lines) > 2 else ""
			verify_iso = lines[3] if len(lines) > 3 else ""
			verify_exposure = lines[4] if len(lines) > 4 else ""
			verify_wb = lines[5] if len(lines) > 5 else ""
			verify_exp_mode = lines[6] if len(lines) > 6 else ""
			verify_image_number = lines[7] if len(lines) > 7 else ""
			verify_desc = lines[8] if len(lines) > 8 else ""
			verify_user_comment = lines[9] if len(lines) > 9 else ""
			comment_status = "empty" if not verify_user_comment else "present"
			self.log(
				"ExifTool metadata written: "
				f"Make={verify_make}, Model={verify_model}, Lens={verify_lens}, "
				f"ISO={verify_iso or 'n/a'}, Exposure={verify_exposure or 'n/a'}, "
				f"WB={verify_wb or 'n/a'}, ExpMode={verify_exp_mode or 'n/a'}, "
				f"ImageNumber={verify_image_number or 'n/a'}, "
				f"UserComment={comment_status}, Description={verify_desc[:120]}"
			)
		except Exception as exc:
			self.log(f"ExifTool exception: {exc}")

	def _ensure_parent_dir(self, path: str) -> None:
		parent = os.path.dirname(path)
		if parent:
			os.makedirs(parent, exist_ok=True)

	def _stringify(self, value: Any) -> str:
		if isinstance(value, (list, tuple, set)):
			return ", ".join(self._stringify(v) for v in value)
		return str(value)

	def log(self, message: str) -> None:
		timestamp = datetime.now().strftime("%H:%M:%S")
		self.log_output.append(f"[{timestamp}] {message}")

	def closeEvent(self, event) -> None:
		try:
			if self.camera:
				self.camera.stop()
			self.recorder.stop()
		except Exception:
			pass
		super().closeEvent(event)


def main() -> int:
	app = QApplication(sys.argv)
	win = CameraTestWindow()
	win.show()

	if win.device_combo.count() == 0:
		QMessageBox.warning(
			win,
			"No camera found",
			"No webcam devices were detected. Connect a webcam and press Refresh.",
		)

	return app.exec()


if __name__ == "__main__":
	raise SystemExit(main())
