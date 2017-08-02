from collections import OrderedDict
import datetime as dt
from functools import partial
import logging
from pathlib import Path
import os


from bokeh import events
from bokeh.colors import RGB
from bokeh.layouts import gridplot
from bokeh.models import (
    Range1d, LinearColorMapper, ColorBar, FixedTicker,
    ColumnDataSource, CustomJS, WMTSTileSource)
from bokeh.models.widgets import Select, Slider
from bokeh.plotting import figure, curdoc
from matplotlib.colors import BoundaryNorm
from matplotlib.ticker import MaxNLocator
from matplotlib.cm import ScalarMappable, get_cmap
import numpy as np
from tornado import gen


MIN_VAL = 0.01
MAX_VAL = 2
ALPHA = 0.7
DATA_DIRECTORY = os.getenv('MRMS_DATADIR', '~/.mrms')


def load_data(date='latest'):
    strformat = '%Y/%m/%d/%HZ.npz'
    dir = os.path.expanduser(DATA_DIRECTORY)
    if date == 'latest':
        p = Path(dir)
        path = sorted([pp for pp in p.rglob('*.npz')], reverse=True)[0]
    else:
        path = os.path.join(dir, date.strftime(strformat))

    valid_date = dt.datetime.strptime(str(path), '{}/{}'.format(dir, strformat))
    data_load = np.load(path)
    regridded_data = data_load['data'] / 25.4  # mm to in
    X = data_load['X']
    Y = data_load['Y']
    masked_regrid = np.ma.masked_less(regridded_data, MIN_VAL).clip(
        max=MAX_VAL)
    return masked_regrid, X, Y, valid_date


def find_all_times():
    p = Path(DATA_DIRECTORY).expanduser()
    out = OrderedDict()
    for pp in sorted(p.rglob('*.npz')):
        try:
            datetime = dt.datetime.strptime(''.join(pp.parts[-4:]),
                                            '%Y%m%d%HZ.npz')
        except ValueError:
            logging.debug('%s does not conform to expected format', pp)
            continue
        date = datetime.strftime('%Y-%m-%d %HZ')
        out[date] = datetime
    return out


# setup the coloring
levels = MaxNLocator(nbins=21).tick_values(0, MAX_VAL)
cmap = get_cmap('viridis')
norm = BoundaryNorm(levels, ncolors=cmap.N, clip=True)
sm = ScalarMappable(norm=norm, cmap=cmap)
color_pal = [RGB(*val).to_hex() for val in
             sm.to_rgba(levels, bytes=True, norm=True)[:-1]]
color_mapper = LinearColorMapper(color_pal, low=sm.get_clim()[0],
                                 high=sm.get_clim()[1])
ticker = FixedTicker(ticks=levels[::3])
cb = ColorBar(color_mapper=color_mapper, location=(0, 0),
              scale_alpha=ALPHA, ticker=ticker)

# make the bokeh figures without the data yet
width = 600
height = 350
sfmt = '%Y-%m-%d %HZ'
tools = 'pan, box_zoom, resize, reset, save'
map_fig = figure(plot_width=width, plot_height=height,
                 y_axis_type=None, x_axis_type=None,
                 toolbar_location='left', tools=tools + ', wheel_zoom',
                 active_scroll='wheel_zoom',
                 title='MRMS Precipitation')

rgba_img_source = ColumnDataSource(data={'image': [], 'x': [], 'y': [],
                                         'dw': [], 'dh': []})
rgba_img = map_fig.image_rgba(image='image', x='x', y='y', dw='dw', dh='dh',
                              source=rgba_img_source)


# Need to use this and not bokeh.tile_providers.STAMEN_TONER
# https://github.com/bokeh/bokeh/issues/4770
STAMEN_TONER = WMTSTileSource(
    url='https://stamen-tiles.a.ssl.fastly.net/toner-lite/{Z}/{X}/{Y}.png',
    attribution=(
        'Map tiles by <a href="http://stamen.com">Stamen Design</a>, '
        'under <a href="http://creativecommons.org/licenses/by/3.0">CC BY 3.0</a>. '
        'Data by <a href="http://openstreetmap.org">OpenStreetMap</a>, '
        'under <a href="http://www.openstreetmap.org/copyright">ODbL</a>'
    )
)
map_fig.add_tile(STAMEN_TONER)
map_fig.add_layout(cb, 'right')

# Make the histogram figure
hist_fig = figure(plot_width=height, plot_height=height,
                  toolbar_location='right',
                  x_axis_label='Precipitation (inches)',
                  y_axis_label='Counts', tools=tools + ', ywheel_zoom',
                  active_scroll='ywheel_zoom',
                  x_range=Range1d(start=0, end=MAX_VAL))

# make histograms
bin_width = levels[1] - levels[0]
bin_centers = levels[:-1] + bin_width / 2
hist_sources = [ColumnDataSource(data={'x': [bin_centers[i]],
                                       'top': [3.0e6],
                                       'color': [color_pal[i]],
                                       'bottom': [0],
                                       'width': [bin_width]})
                for i in range(len(bin_centers))]
for source in hist_sources:
    hist_fig.vbar(x='x', top='top', width='width', bottom='bottom',
                  color='color', fill_alpha=ALPHA, source=source)

line_source = ColumnDataSource(data={'x': [-1, -1], 'y': [0, 1]})
hist_fig.line(x='x', y='y', color='red', source=line_source, alpha=ALPHA)

file_dict = find_all_times()
dates = list(file_dict.keys())[::-1]
select_day = Select(title='Valid Date', value=dates[0], options=dates)

# Setup callbacks for moving line on histogram
pos_source = ColumnDataSource(data={
    'bin_centers': [bin_centers], 'shape': [0],
    'bin': [0], 'dx': [0], 'dy': [0], 'left': [0], 'bottom': [0]})

line_callback = CustomJS(args={'source': pos_source,
                               'lsource': line_source},
                         code="""
var data = source.data;
var x = cb_obj['x'];
var y = cb_obj['y'];

var x_index = 0;
var y_index = 0;

x_index = Math.round((x - data['left'][0])/data['dx'][0]);
y_index = Math.round((y - data['bottom'][0])/data['dy'][0]);
var idx = y_index * data['shape'][0] + x_index;
var bin = data['bin'][0][idx];
var center = data['bin_centers'][0][bin - 1];
var ldata = lsource.data;
xi = ldata['x'];
xi[0] = center;
xi[1] = center;
setTimeout(function(){lsource.change.emit()}, 100);
""")

no_line = CustomJS(args={'lsource': line_source}, code="""
var data = lsource.data;
xi = data['x'];
xi[0] = -1;
xi[1] = -1;
lsource.change.emit();
""")


# Setup the updates for all the data
local_data_source = ColumnDataSource(data={'masked_regrid': [0], 'xn': [0],
                                           'yn': [0],
                                           'valid_date': [dt.datetime.now()]})


def update_histogram(attr, old, new):
    # makes it so only one callback added per 100 ms
    try:
        doc.add_timeout_callback(_update_histogram, 100)
    except ValueError:
        pass


@gen.coroutine
def _update_histogram():
    left = map_fig.x_range.start
    right = map_fig.x_range.end
    bottom = map_fig.y_range.start
    top = map_fig.y_range.end

    masked_regrid = local_data_source.data['masked_regrid'][0]
    xn = local_data_source.data['xn'][0]
    yn = local_data_source.data['yn'][0]

    left_idx = np.abs(xn - left).argmin()
    right_idx = np.abs(xn - right).argmin() + 1
    bottom_idx = np.abs(yn - bottom).argmin()
    top_idx = np.abs(yn - top).argmin() + 1
    logging.debug('Updating histogram...')

    counts, _ = np.histogram(
        masked_regrid[bottom_idx:top_idx, left_idx:right_idx], bins=levels,
        range=(levels.min(), levels.max()))
    line_source.data.update({'y': [0, counts.max()]})
    for i, source in enumerate(hist_sources):
        source.data.update({'top': [counts[i]]})
    logging.debug('Done updating histogram')


def update_map(attr, old, new):
    try:
        doc.add_timeout_callback(_update_histogram, 100)
    except ValueError:
        pass


@gen.coroutine
def _update_map(update_range=False):
    logging.debug('Updating map...')
    valid_date = local_data_source.data['valid_date'][0]
    title = 'MRMS Precipitation {} through {}'.format(
        (valid_date - dt.timedelta(hours=24)).strftime(sfmt),
        valid_date.strftime(sfmt))
    map_fig.title.text = title
    masked_regrid = local_data_source.data['masked_regrid'][0]
    xn = local_data_source.data['xn'][0]
    yn = local_data_source.data['yn'][0]
    rgba_vals = sm.to_rgba(masked_regrid, bytes=True, alpha=ALPHA)
    rgba_img_source.data.update({'image': [rgba_vals], 'x': [xn[0]],
                                 'y': [yn[0]], 'dw': [xn[-1] - xn[0]],
                                 'dh': [yn[-1] - yn[0]]})
    if update_range:
        map_fig.x_range.start = xn[0]
        map_fig.x_range.end = xn[-1]
        map_fig.y_range.start = yn[0]
        map_fig.y_range.end = yn[-1]
    logging.debug('Done updating map')


def update_data(attr, old, new):
    try:
        doc.add_timeout_callback(_update_data, 100)
    except ValueError:
        pass

@gen.coroutine
def _update_data(update_range=False):
    logging.debug('Updating data...')
    date = file_dict[select_day.value]
    masked_regrid, X, Y, valid_date = load_data(date)
    xn = X[0]
    yn = Y[:, 0]
    local_data_source.data.update({'masked_regrid': [masked_regrid],
                                   'xn': [xn], 'yn': [yn],
                                   'valid_date': [valid_date]})
    pos_source.data.update({
        'shape': [masked_regrid.shape[1]],
        'bin': [np.digitize(masked_regrid, levels[:-1]).astype('uint8').ravel()],
        'dx': [xn[1] - xn[0]], 'dy': [yn[1] - yn[0]],
        'left': [xn[0]], 'bottom': [yn[0]]})
    curdoc().add_next_tick_callback(partial(_update_map, update_range))
    curdoc().add_timeout_callback(_update_histogram, 10)
    logging.debug('Done updating data')


map_fig.js_on_event(events.MouseMove, line_callback)
map_fig.js_on_event(events.MouseLeave, no_line)
map_fig.x_range.on_change('start', update_histogram)
map_fig.x_range.on_change('end', update_histogram)
map_fig.y_range.on_change('start', update_histogram)
map_fig.y_range.on_change('end', update_histogram)

select_day.on_change('value', update_data)

# layout the document
lay = gridplot([[select_day], [map_fig, hist_fig]], toolbar_location='left',
               merge_tools=False)
doc = curdoc()
doc.add_root(lay)
doc.add_next_tick_callback(partial(_update_data, True))
