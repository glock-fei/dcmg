import logging
import re
import warnings
from pathlib import Path
from typing import List, Tuple, Union, Optional

import numpy as np
import rasterio
from pydantic import BaseModel, Field
from rasterio.mask import mask
from shapely.geometry import Polygon
from utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class QuadratBase(BaseModel):
    """
    Model for quadrats.
    """
    idx: Optional[str] = Field(None, description="Index of the quadrat")
    name: Optional[str] = Field(None, description="Name of the quadrat")
    sort_no: Optional[int] = Field(None, description="Sort number of the quadrat")
    coords: list[tuple[float, float]] = Field(..., description="Coordinates of the quadrat")
    center: tuple[float, float] = Field(..., description="Center of the quadrat")


class Sampling(BaseModel):
    """
    Model for quadrats.
    """
    title: Optional[str] = Field(None, description="Title of the quadrats")
    project_id: int = Field(..., description="Project ID of odm task")
    task_id: str = Field(..., description="Task ID of odm task")
    # quadrats: List[QuadratBase] = Field(..., description="Quadrats")


class Radiometric(BaseModel):
    """
    Model for radiometric information.
    """
    name: str
    picture: str
    coords: list[tuple[float, float]]
    panel_reflectance: float = Field(..., description="Reflectance coefficient of the reflectance panel")


def extract_black_level(tif_file: Union[str, Path],
                        default_level: float = 4096.0) -> float:
    """
    Extracts the black level from the EXIF metadata of a TIFF file.
    """
    try:
        with rasterio.open(tif_file) as src:
            tags = src.tags()

            for key, value in tags.items():
                if "BlackLevel" in key or "BLACKLEVEL" in key.upper():
                    levels = [float(v) for v in str(value).split() if v.replace(".", "", 1).isdigit()]
                    if levels:
                        return levels[0]

            xmp_tags = src.tags(ns="xml:XMP") or src.tags(ns="XMP")
            if xmp_tags:
                for key, value in xmp_tags.items():
                    if "BlackCurrent" in key or "BlackLevel" in key:
                        return float(value)

        with open(tif_file, "rb") as f:
            header_data = f.read(50000).decode("utf-8", errors="ignore")
            match = re.search(r'BlackCurrent["\'>\s]*([\d.]+)', header_data)
            if match:
                return float(match.group(1))

    except (FileNotFoundError, Exception, rasterio.errors.RasterioIOError) as e:
        logger.warning("Failed to read EXIF black level from %s: %s. Using default.", tif_file, e)

    logger.info("Black level not found in metadata for %s. Using default: %s", tif_file, default_level)

    return default_level


def sort_coordinates_clockwise(
        pixel_coords: List[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    """
    """
    # Convert coordinates to numpy array for easier calculation
    coords: np.ndarray = np.array(pixel_coords)

    # Calculate the center point
    center = coords.mean(axis=0)

    # Calculate the angle of each point relative to the center point
    angles = np.arctan2(coords[:, 1] - center[1], coords[:, 0] - center[0])
    sorted_indices = np.argsort(angles)

    return coords[sorted_indices].tolist()


def get_dn_values_in_polygon(
        coord: list[Tuple[float, float]],
        src: rasterio.io.DatasetReader,
        raise_on_empty: bool = True
) -> tuple[np.ndarray, list[Tuple[float, float]]]:
    """
    Extracts the DN values in the polygon.

    """
    # Sort coordinates in clockwise order
    sorted_coords = sort_coordinates_clockwise(coord)
    polygon = Polygon(sorted_coords)

    # Extract the region using the mask function
    masked_image, out_transform = mask(src, [polygon], crop=True, filled=False)

    # Flatten the image array to 1D and create a mask for valid values
    flattened = masked_image.flatten()
    valid_mask = ~np.isnan(flattened) & np.isfinite(flattened)  # & (flattened > 0)

    # Exclude nodata values if present
    if src.nodata is not None:
        valid_mask &= (flattened != src.nodata)

    # Check if we have any valid values
    if raise_on_empty and not np.any(valid_mask):
        raise ValueError(_("No valid DN values found in the selected panel region"))

    return np.array(flattened[valid_mask]), sorted_coords


def is_coord_in_raster_bounds(
        coord: Tuple[float, float],
        bounds,
        tolerance: float = 1e-9
) -> bool:
    """
    check coord in the raster bounds
    """
    x, y = coord
    left, bottom, right, top = bounds

    return (left - tolerance <= x <= right + tolerance and
            bottom - tolerance <= y <= top + tolerance)


def get_surface_reflectance(
        tif_file: Union[str, Path],
        panel_coords: List[Tuple[float, float]],
        panel_reflectance: float = 0.6
) -> float:
    """
    Used to calculate the surface reflectance of a reflectance panel for a given band.
    Import the reflectance panel photo for the corresponding band, select the panel area, 
    and enter the reflectance coefficient of the reflectance panel (e.g. 0.5).
    The software automatically applies the reflectance panel coefficient to convert 
    the DN values to surface reflectance in the range 0-1.
    """
    black_level = extract_black_level(tif_file)
    logger.info("black level: %.6f", black_level)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=rasterio.errors.NotGeoreferencedWarning)
        # Open the TIFF file
        with rasterio.open(tif_file) as src:
            dn_values, sorted_coords = get_dn_values_in_polygon(panel_coords, src)
            mean_dn = np.mean(dn_values)

            # Check if mean is zero or very close to zero to avoid division by zero
            if np.isclose(mean_dn, 0.0) or np.isnan(mean_dn) or not np.isfinite(mean_dn):
                raise ValueError(_("Mean DN value is zero or very close to zero"))

            # Calculate reflectance coefficient
            surface_reflectance = panel_reflectance / mean_dn
            logger.info("surface reflectance: %.8f , coordinates mean DN: %.6f", surface_reflectance, mean_dn)

            return float(surface_reflectance)
