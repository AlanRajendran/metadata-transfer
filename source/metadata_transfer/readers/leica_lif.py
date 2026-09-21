"""Leica LIF reader: pixels via `liffile`, metadata from the Leica XML.

Channel <-> acquisition-setting mapping rule (verified on SP8 / LAS X 3.5.5 data,
see docs/mapping_table.md):

* Channels are stored in sequential-setting order; within a setting, one channel per
  *active* detector, in detector-list order.
* A detector's emission window is the MultiBand with the same `Channel` number in that
  setting; the transmitted-light detector (ScanType TLD / Channel 100) has none.
* Cross-check: the setting's LUT for that detector channel must equal the image's
  ChannelDescription LUTName at the same position. A mismatch produces a warning.
"""

from __future__ import annotations

from typing import Iterator
from xml.etree import ElementTree as ET

import liffile
import numpy as np

from ..mapping.channel_naming import default_name, make_unique, settings_label
from ..mapping.colors import lut_to_rgb
from ..mapping.orientation import orient_plane
from ..model import (
    Acquisition,
    Calibration,
    Channel,
    Dimensions,
    Objective,
    Orientation,
    SeriesInfo,
    SeriesMetadata,
)
from .base import Reader

_SUPPORTED_DIMS = {"T", "Z", "C", "Y", "X"}
_DIM_REASONS = {
    "M": "tile scan with unmerged tiles",
    "λ": "lambda (emission) scan",
    "Λ": "excitation (lambda) scan",
    "S": "RGB colour image",
    "A": "rotation scan",
    "N": "XT slices",
    "Q": "T slices",
    "L": "loop",
}


def _f(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _is_true(value: str | None) -> bool:
    return str(value).strip().lower() in ("1", "true")


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _step(dd: ET.Element | None) -> float | None:
    """Physical step of a Leica dimension (Length / (N - 1)), in µm for metres, s for seconds."""
    if dd is None:
        return None
    n = int(dd.get("NumberOfElements") or 1)
    length = _f(dd.get("Length"))
    if n < 2 or length is None:
        return None
    step = length / (n - 1)
    unit = (dd.get("Unit") or "").strip()
    if unit == "m":
        return step * 1e6
    return step


class LeicaLifReader(Reader):
    extensions = (".lif",)
    format_name = "Leica LIF"

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._lif = liffile.LifFile(path)
        self._images = list(self._lif.images)

    def close(self) -> None:
        self._lif.close()

    # ------------------------------------------------------------------ listing
    def list_series(self) -> list[SeriesInfo]:
        out: list[SeriesInfo] = []
        for i, im in enumerate(self._images):
            sizes = dict(im.sizes)
            dims = Dimensions(
                x=sizes.get("X", 1), y=sizes.get("Y", 1), z=sizes.get("Z", 1), c=sizes.get("C", 1), t=sizes.get("T", 1)
            )
            supported, reason, dtype = True, "", ""
            try:
                dtype = str(im.dtype)
            except Exception as exc:  # heterogeneous channel types etc.
                supported, reason = False, f"unreadable pixel type ({exc})"
            if im.is_flim:
                supported, reason = False, "FLIM/TCSPC data is not supported in this version"
            elif supported:
                extra = [d for d in im.dims if d not in _SUPPORTED_DIMS]
                if extra:
                    what = ", ".join(_DIM_REASONS.get(d, f"dimension '{d}'") for d in extra)
                    supported, reason = False, f"{what} is not supported in this version"
                elif dtype not in ("uint8", "uint16"):
                    supported, reason = False, f"pixel type {dtype} is not supported in this version"
            out.append(
                SeriesInfo(
                    index=i,
                    name=im.name,
                    path=getattr(im, "path", im.name),
                    dims=dims,
                    dtype=dtype,
                    supported=supported,
                    reason=reason,
                )
            )
        return out

    # ----------------------------------------------------------------- metadata
    def metadata(self, index: int, *, apply_orientation: bool = False, level: int = 0) -> SeriesMetadata:
        im = self._images[index]
        image_el = im.xml_element.find("./Data/Image")
        desc = image_el.find("./ImageDescription")
        chan_descs = desc.findall("./Channels/ChannelDescription")
        dim_descs = {int(d.get("DimID")): d for d in desc.findall("./Dimensions/DimensionDescription")}
        sizes = dict(im.sizes)
        dims = Dimensions(
            x=sizes.get("X", 1), y=sizes.get("Y", 1), z=sizes.get("Z", 1), c=sizes.get("C", 1), t=sizes.get("T", 1)
        )
        warnings: list[str] = []

        bits = max((int(cd.get("Resolution") or 0) for cd in chan_descs), default=0) or im.dtype.itemsize * 8
        z_step = _step(dim_descs.get(3))
        calibration = Calibration(
            pixel_size_x_um=_step(dim_descs.get(1)),
            pixel_size_y_um=_step(dim_descs.get(2)),
            z_step_um=abs(z_step) if z_step else None,
            time_step_s=_step(dim_descs.get(4)),
        )
        z_positions = None
        if "Z" in im.coords and dims.z > 1:
            z_positions = [float(v) * 1e6 for v in im.coords["Z"]]

        hw = image_el.find("./Attachment[@Name='HardwareSetting']")
        master, settings, imaging_mode = self._acquisition_settings(hw)
        ref = master if master is not None else (settings[0] if settings else None)
        if ref is None:
            warnings.append("No acquisition settings found in the Leica metadata; only geometry is transferred.")

        objective = Objective()
        orientation = Orientation(apply=apply_orientation)
        zoom = pinhole_um = None
        scan: dict[str, str] = {}
        if ref is not None:
            objective = Objective(
                name=_clean(ref.get("ObjectiveName")),
                magnification=_f(ref.get("Magnification")),
                numerical_aperture=_f(ref.get("NumericalAperture")),
                immersion=_clean(ref.get("Immersion")).title(),
                refractive_index=_f(ref.get("RefractionIndex")),
            )
            zoom = _f(ref.get("Zoom"))
            ph = _f(ref.get("Pinhole"))
            pinhole_um = ph * 1e6 if ph is not None else None
            orientation = Orientation(
                flip_x=_is_true(ref.get("FlipX")),
                flip_y=_is_true(ref.get("FlipY")),
                swap_xy=_is_true(ref.get("SwapXY")),
                apply=apply_orientation,
            )
            scan = self._scan_settings(ref, hw)

        channels, verified = self._channels(image_el, chan_descs, settings, warnings)
        acquisition = self._acquisition(im, hw, ref, dims)

        vendor_raw = {
            "format": "Leica LIF",
            "container_path": getattr(im, "path", im.name),
            "flags": {"FlipX": orientation.flip_x, "FlipY": orientation.flip_y, "SwapXY": orientation.swap_xy},
            "channel_mapping_verified": verified,
            "sequential_settings": [self._describe_setting(s) for s in settings],
            "display_scaling": [
                {k: _f(csi.get(k)) for k in ("BlackValue", "WhiteValue", "GammaValue")}
                for csi in image_el.findall("./Attachment[@Name='ViewerScaling']/ChannelScalingInfo")
            ],
        }

        return SeriesMetadata(
            source_path=self.path,
            source_format=self.format_name,
            series_index=index,
            series_name=im.name,
            dims=dims,
            dtype=str(im.dtype),
            bits=bits,
            calibration=calibration,
            objective=objective,
            channels=channels,
            imaging_mode=imaging_mode,
            zoom=zoom,
            pinhole_um=pinhole_um,
            orientation=orientation,
            acquisition=acquisition,
            z_positions_um=z_positions,
            scan_settings=scan,
            vendor_raw=vendor_raw,
            warnings=warnings,
        )

    @staticmethod
    def _acquisition_settings(hw: ET.Element | None) -> tuple[ET.Element | None, list[ET.Element], str]:
        if hw is None:
            return None, [], "widefield"
        seq_list = hw.find(".//LDM_Block_Sequential/LDM_Block_Sequential_List")
        master = hw.find(".//LDM_Block_Sequential/LDM_Block_Sequential_Master/ATLConfocalSettingDefinition")
        if seq_list is not None and len(seq_list):
            settings = [s for s in seq_list if s.tag == "ATLConfocalSettingDefinition"]
            return master, settings, "laser_scanning_confocal"
        confocal = hw.find(".//ATLConfocalSettingDefinition")
        if confocal is not None:
            return confocal, [confocal], "laser_scanning_confocal"
        camera = hw.find(".//ATLCameraSettingDefinition")
        return camera, [], "widefield"

    @staticmethod
    def _lasers(setting: ET.Element) -> list[tuple[float, float]]:
        lines = setting.findall("./AotfList/Aotf/LaserLineSetting") or list(setting.iter("LaserLineSetting"))
        active = []
        for ll in lines:
            line, intensity = _f(ll.get("LaserLine")), _f(ll.get("IntensityDev"))
            if line and intensity and intensity > 0:
                active.append((line, intensity))
        return active

    @staticmethod
    def _detectors(setting: ET.Element) -> list[ET.Element]:
        dets = setting.findall("./DetectorList/Detector") or list(setting.iter("Detector"))
        return [d for d in dets if d.get("IsActive") == "1"]

    def _channels(
        self, image_el: ET.Element, chan_descs: list[ET.Element], settings: list[ET.Element], warnings: list[str]
    ) -> tuple[list[Channel], bool]:
        image_luts = [cd.get("LUTName", "") for cd in chan_descs]
        derived: list[Channel] = []
        for si, s in enumerate(settings):
            lasers = self._lasers(s)
            bands = {mb.get("Channel"): mb for mb in (s.findall("./Spectro/MultiBand") or list(s.iter("MultiBand")))}
            luts = {lut.get("Channel"): lut.get("LutName", "") for lut in s.findall("./LUT_List/LUT")}
            ph = _f(s.get("Pinhole"))
            for det in self._detectors(s):
                chno = det.get("Channel")
                name = det.get("Name", "")
                is_trans = det.get("ScanType") == "TLD" or chno == "100" or "trans" in name.lower()
                band = None if is_trans else bands.get(chno)
                em = None
                if band is not None:
                    lo, hi = _f(band.get("LeftWorld")), _f(band.get("RightWorld"))
                    if lo is not None and hi is not None:
                        em = (round(lo, 1), round(hi, 1))
                ex, pct = self._pick_laser(lasers, em)
                derived.append(
                    Channel(
                        name="",
                        excitation_nm=ex,
                        emission_range_nm=em,
                        color_name=luts.get(chno, ""),
                        modality="brightfield" if is_trans else "fluorescence",
                        detector=name,
                        detector_type=det.get("Type", ""),
                        detector_gain=_f(det.get("Gain")),
                        detector_offset=_f(det.get("Offset")),
                        laser_intensity_pct=pct,
                        dye_name=(band.get("DyeName") or "") if band is not None else "",
                        pinhole_um=ph * 1e6 if ph is not None else None,
                        sequential_index=si,
                    )
                )

        verified = False
        if derived and len(derived) == len(chan_descs):
            mismatch = [
                i + 1 for i, (ch, lut) in enumerate(zip(derived, image_luts)) if ch.color_name.lower() != lut.lower()
            ]
            if mismatch:
                warnings.append(
                    "Channel/acquisition-setting mapping could not be confirmed for channel(s) "
                    f"{', '.join(map(str, mismatch))} (colour tables differ); please check wavelengths."
                )
            else:
                verified = True
            channels = derived
        else:
            if settings:
                warnings.append(
                    f"Found {len(derived)} active detectors for {len(chan_descs)} channels; "
                    "wavelength/detector details were not assigned to channels."
                )
            channels = [Channel(name="") for _ in chan_descs]

        scaling = image_el.findall("./Attachment[@Name='ViewerScaling']/ChannelScalingInfo")
        for i, (ch, cd) in enumerate(zip(channels, chan_descs)):
            ch.color_name = cd.get("LUTName", "") or ch.color_name
            ch.color_rgb = lut_to_rgb(ch.color_name)
            if i < len(scaling):
                b, w = _f(scaling[i].get("BlackValue")), _f(scaling[i].get("WhiteValue"))
                if b is not None and w is not None:
                    ch.display_range = (b, w)
            ch.auto_name = settings_label(ch)
            ch.name = default_name(ch) if (ch.detector or ch.dye_name) else f"Channel {i + 1} ({ch.color_name})"
        for ch, n in zip(channels, make_unique([c.name for c in channels])):
            ch.name = n
        return channels, verified

    @staticmethod
    def _pick_laser(lasers: list[tuple[float, float]], em: tuple[float, float] | None) -> tuple[float | None, float | None]:
        """Excitation = the longest active laser line below the emission window."""
        if not lasers:
            return None, None
        if em is not None:
            below = [ll for ll in lasers if ll[0] < em[0] + 5]
            if below:
                return max(below)
        return max(lasers)

    @staticmethod
    def _scan_settings(ref: ET.Element, hw: ET.Element | None) -> dict[str, str]:
        out: dict[str, str] = {}

        def put(label: str, attr: str, fmt=lambda v: v) -> None:
            v = ref.get(attr)
            if v not in (None, ""):
                out[label] = fmt(v)

        put("Scan mode", "ScanMode")
        put("Scan speed (Hz)", "ScanSpeed")
        put("Pixel dwell time (µs)", "PixelDwellTime", lambda v: f"{float(v) * 1e6:.3g}")
        put("Zoom", "Zoom", lambda v: f"{float(v):.2f}")
        put("Pinhole (Airy units)", "PinholeAiry", lambda v: f"{float(v):.2f}")
        put("Line average", "LineAverage")
        put("Frame average", "FrameAverage")
        put("Line accumulation", "Line_Accumulation")
        put("Frame accumulation", "FrameAccumulation")
        put("Scan direction", "ScanDirectionXName")
        put("Z-stack direction", "ZStackDirectionModeName")
        if hw is not None:
            seq = hw.find(".//LDM_Block_Sequential")
            if seq is not None:
                n = len(seq.findall("./LDM_Block_Sequential_List/ATLConfocalSettingDefinition"))
                if n:
                    out["Sequential settings"] = str(n)
        return out

    def _describe_setting(self, s: ET.Element) -> dict:
        return {
            "lasers": [{"line_nm": ll, "intensity_pct": round(p, 2)} for ll, p in self._lasers(s)],
            "detectors": [
                {
                    "name": d.get("Name"),
                    "type": d.get("Type"),
                    "channel": d.get("Channel"),
                    "gain": _f(d.get("Gain")),
                    "offset": _f(d.get("Offset")),
                    "mode": d.get("AcquisitionModeName"),
                }
                for d in self._detectors(s)
            ],
            "bands": [
                {
                    "channel": mb.get("Channel"),
                    "from_nm": _f(mb.get("LeftWorld")),
                    "to_nm": _f(mb.get("RightWorld")),
                    "dye": mb.get("DyeName"),
                }
                for mb in s.findall("./Spectro/MultiBand")
            ],
            "pinhole_um": (_f(s.get("Pinhole")) or 0) * 1e6 or None,
            "line_average": s.get("LineAverage"),
            "frame_average": s.get("FrameAverage"),
        }

    def _acquisition(self, im, hw: ET.Element | None, ref: ET.Element | None, dims: Dimensions) -> Acquisition:
        acq = Acquisition()
        ts = im.timestamps
        if ts is not None and len(ts):
            acq.start = ts[0].astype("datetime64[ms]").item()
            frames = dims.t * dims.z
            n = len(ts)
            if n >= frames and n % frames == 0:
                per = n // frames
                sel = ts[::per][:frames]
                acq.frame_times_s = ((sel - ts[0]) / np.timedelta64(1, "ms") / 1000.0).astype(float).tolist()
        elif self._lif.datetime is not None:
            acq.start = self._lif.datetime.replace(tzinfo=None)
        if ref is not None:
            for attr, field in (("StagePosX", "stage_x_um"), ("StagePosY", "stage_y_um"), ("ZPosition", "stage_z_um")):
                v = _f(ref.get(attr))
                if v is not None:
                    setattr(acq, field, v * 1e6)
            model = ref.get("MicroscopeModel", "")
        else:
            model = ""
        if hw is not None:
            system = hw.get("SystemTypeName", "")
            acq.microscope = f"Leica {system}".strip() + (f" ({model})" if model else "")
            acq.software = hw.get("Software", "")
            acq.serial_number = (ref.get("SystemSerialNumber", "") if ref is not None else "") or ""
        return acq

    # ------------------------------------------------------------------- pixels
    def iter_frames(self, meta: SeriesMetadata) -> Iterator[tuple[int, int, int, np.ndarray]]:
        im = self._images[meta.series_index]
        sizes = dict(im.sizes)
        out_dims = meta.output_dims
        for t in range(meta.dims.t):
            for z in range(meta.dims.z):
                frame = np.empty((out_dims.y, out_dims.x, meta.dims.c), dtype=im.dtype)
                for c in range(meta.dims.c):
                    idx = {k: v for k, v in (("T", t), ("Z", z), ("C", c)) if k in sizes}
                    frame[..., c] = orient_plane(im.frame(**idx), meta.orientation)
                yield t, 0, z, frame

    def raw_metadata_xml(self, index: int) -> str:
        return ET.tostring(self._images[index].xml_element, encoding="unicode")
