from __future__ import annotations

import argparse
import math
import os
import shutil
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Literal
from typing import NamedTuple

import cartopy.crs as ccrs
import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib_scalebar.scalebar import ScaleBar
from PIL import ExifTags
from PIL import Image
from shapely import Point
from shapely.geometry.polygon import Polygon

STATIONS = gpd.GeoDataFrame.from_dict(
    {
        'saarlandstr_open_space_vegetation': Point(7.46981, 51.50711),
        'landgrafenstr_vegetation': Point(7.47259, 51.50424),
        'chemnitzerstr_n_s_street': Point(7.46328, 51.50198),
        'saarlandstr_e_w_street': Point(7.45984, 51.50463),
        'eintrachtstr_open_space': Point(7.46882, 51.50054),
        'landgrafenstr_e_w_vegetation': Point(7.46261, 51.50275),
        'DOTAMW': Point(7.461792881387834, 51.50175339515813),
    },
    orient='index',
    crs='EPSG:4326',
    columns=['geometry'],
).to_crs(crs='EPSG:25832').buffer(35).to_frame()


def scale_bar(
        gdf: gpd.GeoDataFrame,
        to_crs: int = 25832,
        **kwargs: Any,
) -> ScaleBar:
    # we need to roughly get the distance in meters for the scale bar
    geom = gdf.geometry.iloc[0]
    if isinstance(geom, Polygon):
        point_1 = Point(geom.bounds[0], geom.bounds[1])
    elif isinstance(geom, Point):
        point_1 = geom
    else:
        raise ValueError(f'unknonw geom type {type(geom)!r}')

    point_2 = Point(point_1.x + 1, point_1.y)
    points = gpd.GeoSeries([point_1, point_2], crs=gdf.crs)
    points = points.to_crs(to_crs)
    distance_meters = points[0].distance(points[1])
    return ScaleBar(distance_meters, **kwargs)


def deg_min_secs_to_decimal(
        degrees: float,
        minutes: float,
        seconds: float,
        reference: Literal['N', 'S', 'E', 'W'],
) -> float:
    decimal_degrees = degrees + (minutes / 60) + (seconds / 3600)
    if reference == 'S' or reference == 'W':
        return -decimal_degrees
    else:
        return decimal_degrees


class Photo(NamedTuple):
    img: Image
    date: datetime
    width: int
    height: int
    orientation: Literal[1, 8, 3, 6]
    latitude: float
    longitude: float
    altitude: float

    @property
    def has_position(self) -> bool:
        return not (math.isnan(self.latitude) or math.isnan(self.longitude))

    @property
    def position_Z(self) -> Point | None:
        if self.has_position:
            return Point(self.longitude, self.latitude, self.altitude)
        else:
            return None

    @property
    def position(self) -> Point | None:
        if self.has_position:
            return Point(self.longitude, self.latitude)
        else:
            return None

    @classmethod
    def from_image(cls, img: Image) -> Photo:
        tags = img._getexif()
        named_tags = {
            ExifTags.TAGS[k]: v for k, v in tags.items() if k in ExifTags.TAGS
        }
        try:
            gps = {
                ExifTags.GPSTAGS[k]: v for k,
                v in named_tags['GPSInfo'].items()
            }
        except KeyError:
            print('WARNING: image () has no GPS Information')
            gps = {}

        try:
            lat = deg_min_secs_to_decimal(
                degrees=float(gps['GPSLatitude'][0]),
                minutes=float(gps['GPSLatitude'][1]),
                seconds=float(gps['GPSLatitude'][2]),
                reference=gps['GPSLatitudeRef'],
            )
        except KeyError:
            print('WARNING: image has no valid latitude')
            lat = float('nan')
        try:
            lon = deg_min_secs_to_decimal(
                degrees=float(gps['GPSLongitude'][0]),
                minutes=float(gps['GPSLongitude'][1]),
                seconds=float(gps['GPSLongitude'][2]),
                reference=gps['GPSLongitudeRef'],
            )
        except KeyError:
            print('WARNING: image has no valid longitude')
            lon = float('nan')

        try:
            alt = float(gps['GPSAltitude']) + float(gps['GPSAltitudeRef'])
        except (KeyError, ValueError):
            print('WARNING: image has no valid altitude')
            alt = float('nan')

        return cls(
            img=img,
            date=datetime.strptime(
                f'{named_tags["DateTime"]}.{int(named_tags.get("SubsecTime", 0))}{named_tags["OffsetTime"]}',  # noqa: E501
                '%Y:%m:%d %H:%M:%S.%f%z',
            ),
            width=named_tags['ExifImageWidth'],
            height=named_tags['ExifImageHeight'],
            orientation=named_tags['Orientation'],
            latitude=lat,
            longitude=lon,
            altitude=alt,
        )

    def to_geodataframe(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame.from_dict(
            data={
                'date': [self.date],
                'width': [self.width],
                'height': [self.height],
                'orientation': [self.orientation],
                'altitude': [self.altitude],
            },
            geometry=[self.position] if self.has_position else [None],
            crs='EPSG:4326',
        )


class NameSpace(argparse.Namespace):
    files: str
    output_dir: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Rename bike pictures based on nearest station and timestamp.',
    )
    parser.add_argument(
        'files',
        type=str,
        help='The input images any number can be specified. It may be a glob pattern',
        nargs='+',
    )
    parser.add_argument(
        '--output-dir',
        default='images-renamed',
        type=str,
        help='Directory to save the renamed images to',
    )
    args = parser.parse_args(namespace=NameSpace())
    crs = ccrs.epsg(STATIONS.crs.to_epsg())
    _, ax = plt.subplots(figsize=(8, 8), subplot_kw={'projection': crs})
    ax = STATIONS.plot(ax=ax, facecolor=(0, 0, 1, 0.1), edgecolor='blue', linewidth=2)
    cx.add_basemap(ax, crs='EPSG:25832')
    photo_points = []
    for idx, fname in enumerate(args.files):
        print(f'{idx + 1}/{len(args.files)} - {fname}')
        if idx == len(args.files) - 1:
            break
        with Image.open(fname) as img:
            img_obj = Photo.from_image(img)

        photo_point = img_obj.to_geodataframe().to_crs('EPSG:25832').sjoin(
            STATIONS,
            how='inner',
            predicate='within',
        )
        if photo_point.empty:
            print(f'No station found for image: {fname}, skipping!')
            continue
        station_id = photo_point.index_right.values[0]
        date_taken = img_obj.date.astimezone(
            timezone.utc,
        ).strftime('%Y-%m-%dT%H:%M:%SZ')
        new_name = f'{station_id}_{date_taken}.jpg'
        os.makedirs(args.output_dir, exist_ok=True)
        new_path = os.path.join(args.output_dir, new_name)
        shutil.copy(fname, new_path)
        photo_points.append(photo_point)
    if photo_points:
        all_photo_points = gpd.GeoDataFrame(pd.concat(photo_points), crs='EPSG:25832')
        all_photo_points.plot(
            ax=ax,
            color='red',
            alpha=0.7,
            markersize=20,
            edgecolor='black',
            lw=0.25,
            label='Photo',
        )
    ax.legend()
    ax.add_artist(
        scale_bar(
            STATIONS, to_crs=STATIONS.crs.to_epsg(),
            location='upper left', box_alpha=0.6,
        ),
    )
    gl = ax.gridlines(draw_labels=True, linestyle=(0, (2, 2)))
    gl.top_labels = False
    gl.right_labels = False
    plt.tight_layout()
    plt.savefig('stations.png', bbox_inches='tight', dpi=140)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
