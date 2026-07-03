/* Native surface voxelizer for Voxelator.
 *
 * Mirrors the pure-Python SAT triangle/box implementation in voxelator.py
 * (_tri_box_overlap / _plane_box_overlap / _build_occupied_cells_py).
 *
 * Compiled automatically by voxelator.py on first run:
 *   cc -O3 -fopenmp -shared -fPIC -o libvoxelize.so voxelize_native.c
 *
 * C ABI only; loaded through ctypes so it works with any Blender Python.
 */

#include <math.h>
#include <stdint.h>

static int plane_box_overlap(double nx, double ny, double nz,
                             double vx, double vy, double vz,
                             double mx, double my, double mz)
{
    double vmin_x, vmax_x, vmin_y, vmax_y, vmin_z, vmax_z;

    if (nx > 0.0) { vmin_x = -mx - vx; vmax_x =  mx - vx; }
    else          { vmin_x =  mx - vx; vmax_x = -mx - vx; }

    if (ny > 0.0) { vmin_y = -my - vy; vmax_y =  my - vy; }
    else          { vmin_y =  my - vy; vmax_y = -my - vy; }

    if (nz > 0.0) { vmin_z = -mz - vz; vmax_z =  mz - vz; }
    else          { vmin_z =  mz - vz; vmax_z = -mz - vz; }

    if (nx * vmin_x + ny * vmin_y + nz * vmin_z > 0.0)
        return 0;
    if (nx * vmax_x + ny * vmax_y + nz * vmax_z >= 0.0)
        return 1;
    return 0;
}

#define MIN3(a, b, c) ((a) < (b) ? ((a) < (c) ? (a) : (c)) : ((b) < (c) ? (b) : (c)))
#define MAX3(a, b, c) ((a) > (b) ? ((a) > (c) ? (a) : (c)) : ((b) > (c) ? (b) : (c)))

static int axis_test(double axv, double ayv, double azv,
                     double v0x, double v0y, double v0z,
                     double v1x, double v1y, double v1z,
                     double v2x, double v2y, double v2z,
                     double hx, double hy, double hz)
{
    double p0 = axv * v0x + ayv * v0y + azv * v0z;
    double p1 = axv * v1x + ayv * v1y + azv * v1z;
    double p2 = axv * v2x + ayv * v2y + azv * v2z;
    double min_p = MIN3(p0, p1, p2);
    double max_p = MAX3(p0, p1, p2);
    double rad = hx * fabs(axv) + hy * fabs(ayv) + hz * fabs(azv);
    return !(min_p > rad || max_p < -rad);
}

static int tri_box_overlap(double cx, double cy, double cz,
                           double hx, double hy, double hz,
                           double ax, double ay, double az,
                           double bx, double by, double bz,
                           double c2x, double c2y, double c2z)
{
    double v0x = ax - cx, v0y = ay - cy, v0z = az - cz;
    double v1x = bx - cx, v1y = by - cy, v1z = bz - cz;
    double v2x = c2x - cx, v2y = c2y - cy, v2z = c2z - cz;

    double e0x = v1x - v0x, e0y = v1y - v0y, e0z = v1z - v0z;
    double e1x = v2x - v1x, e1y = v2y - v1y, e1z = v2z - v1z;
    double e2x = v0x - v2x, e2y = v0y - v2y, e2z = v0z - v2z;

#define AXIS(axv, ayv, azv) \
    if (!axis_test((axv), (ayv), (azv), v0x, v0y, v0z, v1x, v1y, v1z, v2x, v2y, v2z, hx, hy, hz)) \
        return 0;

    AXIS(0.0, -e0z, e0y)
    AXIS(e0z, 0.0, -e0x)
    AXIS(-e0y, e0x, 0.0)
    AXIS(0.0, -e1z, e1y)
    AXIS(e1z, 0.0, -e1x)
    AXIS(-e1y, e1x, 0.0)
    AXIS(0.0, -e2z, e2y)
    AXIS(e2z, 0.0, -e2x)
    AXIS(-e2y, e2x, 0.0)
#undef AXIS

    double min_x = MIN3(v0x, v1x, v2x);
    double max_x = MAX3(v0x, v1x, v2x);
    if (min_x > hx || max_x < -hx)
        return 0;

    double min_y = MIN3(v0y, v1y, v2y);
    double max_y = MAX3(v0y, v1y, v2y);
    if (min_y > hy || max_y < -hy)
        return 0;

    double min_z = MIN3(v0z, v1z, v2z);
    double max_z = MAX3(v0z, v1z, v2z);
    if (min_z > hz || max_z < -hz)
        return 0;

    double nx = e0y * e1z - e0z * e1y;
    double ny = e0z * e1x - e0x * e1z;
    double nz = e0x * e1y - e0y * e1x;
    if (!plane_box_overlap(nx, ny, nz, v0x, v0y, v0z, hx, hy, hz))
        return 0;

    return 1;
}

/* Marks occupied cells in occ (dx*dy*dz bytes, x-major: idx = (ix*dy + iy)*dz + iz).
 * verts: n_verts * 3 doubles in world space.
 * tris: n_tris * 3 vertex indices.
 * Returns 0 on success, -1 on bad arguments. */
int voxelize_surface(const double *verts, int64_t n_verts,
                     const int64_t *tris, int64_t n_tris,
                     double cell_len,
                     double gmin_x, double gmin_y, double gmin_z,
                     int64_t dx, int64_t dy, int64_t dz,
                     uint8_t *occ)
{
    if (!verts || !tris || !occ || n_verts < 0 || n_tris < 0)
        return -1;
    if (cell_len <= 0.0 || dx <= 0 || dy <= 0 || dz <= 0)
        return -1;

    const double half = 0.5 * cell_len;

#pragma omp parallel for schedule(dynamic, 64)
    for (int64_t ti = 0; ti < n_tris; ti++) {
        int64_t ia = tris[ti * 3 + 0];
        int64_t ib = tris[ti * 3 + 1];
        int64_t ic = tris[ti * 3 + 2];
        if (ia < 0 || ia >= n_verts || ib < 0 || ib >= n_verts || ic < 0 || ic >= n_verts)
            continue;

        double ax = verts[ia * 3 + 0], ay = verts[ia * 3 + 1], az = verts[ia * 3 + 2];
        double bx = verts[ib * 3 + 0], by = verts[ib * 3 + 1], bz = verts[ib * 3 + 2];
        double cx2 = verts[ic * 3 + 0], cy2 = verts[ic * 3 + 1], cz2 = verts[ic * 3 + 2];

        double min_x = MIN3(ax, bx, cx2);
        double min_y = MIN3(ay, by, cy2);
        double min_z = MIN3(az, bz, cz2);
        double max_x = MAX3(ax, bx, cx2);
        double max_y = MAX3(ay, by, cy2);
        double max_z = MAX3(az, bz, cz2);

        int64_t ix0 = (int64_t)floor((min_x - gmin_x) / cell_len) - 1;
        int64_t iy0 = (int64_t)floor((min_y - gmin_y) / cell_len) - 1;
        int64_t iz0 = (int64_t)floor((min_z - gmin_z) / cell_len) - 1;
        int64_t ix1 = (int64_t)floor((max_x - gmin_x) / cell_len) + 1;
        int64_t iy1 = (int64_t)floor((max_y - gmin_y) / cell_len) + 1;
        int64_t iz1 = (int64_t)floor((max_z - gmin_z) / cell_len) + 1;

        if (ix0 < 0) ix0 = 0;
        if (iy0 < 0) iy0 = 0;
        if (iz0 < 0) iz0 = 0;
        if (ix1 > dx - 1) ix1 = dx - 1;
        if (iy1 > dy - 1) iy1 = dy - 1;
        if (iz1 > dz - 1) iz1 = dz - 1;

        if (ix1 < ix0 || iy1 < iy0 || iz1 < iz0)
            continue;

        for (int64_t ix = ix0; ix <= ix1; ix++) {
            double ccx = gmin_x + ((double)ix + 0.5) * cell_len;
            for (int64_t iy = iy0; iy <= iy1; iy++) {
                double ccy = gmin_y + ((double)iy + 0.5) * cell_len;
                for (int64_t iz = iz0; iz <= iz1; iz++) {
                    int64_t idx = (ix * dy + iy) * dz + iz;
                    if (occ[idx])
                        continue;
                    double ccz = gmin_z + ((double)iz + 0.5) * cell_len;
                    if (tri_box_overlap(ccx, ccy, ccz, half, half, half,
                                        ax, ay, az, bx, by, bz, cx2, cy2, cz2))
                        occ[idx] = 1;
                }
            }
        }
    }

    return 0;
}
