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
#include <stdlib.h>
#include <string.h>

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

/* ---------------------------------------------------------------------------
 * Native nearest-triangle color mapping for the bake path.
 *
 * For each query point (voxel cube center in source-local space) find the
 * closest triangle, interpolate its per-corner bake UVs at the closest point,
 * and bilinearly sample the bake image.
 * ------------------------------------------------------------------------ */

static double dot3(const double a[3], const double b[3])
{
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

/* Closest point on triangle abc to p (Ericson, Real-Time Collision Detection).
 * Writes barycentric weights (relative to a,b,c) and returns squared distance. */
static double closest_point_tri(const double p[3], const double a[3],
                                const double b[3], const double c[3],
                                double bary[3])
{
    double ab[3] = { b[0] - a[0], b[1] - a[1], b[2] - a[2] };
    double ac[3] = { c[0] - a[0], c[1] - a[1], c[2] - a[2] };
    double ap[3] = { p[0] - a[0], p[1] - a[1], p[2] - a[2] };
    double q[3];

    double d1 = dot3(ab, ap);
    double d2 = dot3(ac, ap);
    if (d1 <= 0.0 && d2 <= 0.0) {
        bary[0] = 1.0; bary[1] = 0.0; bary[2] = 0.0;
        q[0] = a[0]; q[1] = a[1]; q[2] = a[2];
        goto done;
    }

    {
        double bp[3] = { p[0] - b[0], p[1] - b[1], p[2] - b[2] };
        double d3 = dot3(ab, bp);
        double d4 = dot3(ac, bp);
        if (d3 >= 0.0 && d4 <= d3) {
            bary[0] = 0.0; bary[1] = 1.0; bary[2] = 0.0;
            q[0] = b[0]; q[1] = b[1]; q[2] = b[2];
            goto done;
        }

        double vc = d1 * d4 - d3 * d2;
        if (vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0) {
            double v = d1 / (d1 - d3);
            bary[0] = 1.0 - v; bary[1] = v; bary[2] = 0.0;
            q[0] = a[0] + v * ab[0]; q[1] = a[1] + v * ab[1]; q[2] = a[2] + v * ab[2];
            goto done;
        }

        double cp[3] = { p[0] - c[0], p[1] - c[1], p[2] - c[2] };
        double d5 = dot3(ab, cp);
        double d6 = dot3(ac, cp);
        if (d6 >= 0.0 && d5 <= d6) {
            bary[0] = 0.0; bary[1] = 0.0; bary[2] = 1.0;
            q[0] = c[0]; q[1] = c[1]; q[2] = c[2];
            goto done;
        }

        double vb = d5 * d2 - d1 * d6;
        if (vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0) {
            double w = d2 / (d2 - d6);
            bary[0] = 1.0 - w; bary[1] = 0.0; bary[2] = w;
            q[0] = a[0] + w * ac[0]; q[1] = a[1] + w * ac[1]; q[2] = a[2] + w * ac[2];
            goto done;
        }

        double va = d3 * d6 - d5 * d4;
        if (va <= 0.0 && (d4 - d3) >= 0.0 && (d5 - d6) >= 0.0) {
            double w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
            bary[0] = 0.0; bary[1] = 1.0 - w; bary[2] = w;
            q[0] = b[0] + w * (c[0] - b[0]);
            q[1] = b[1] + w * (c[1] - b[1]);
            q[2] = b[2] + w * (c[2] - b[2]);
            goto done;
        }

        {
            double denom = 1.0 / (va + vb + vc);
            double v = vb * denom;
            double w = vc * denom;
            bary[0] = 1.0 - v - w; bary[1] = v; bary[2] = w;
            q[0] = a[0] + ab[0] * v + ac[0] * w;
            q[1] = a[1] + ab[1] * v + ac[1] * w;
            q[2] = a[2] + ab[2] * v + ac[2] * w;
        }
    }

done:
    {
        double dxp = p[0] - q[0];
        double dyp = p[1] - q[1];
        double dzp = p[2] - q[2];
        return dxp * dxp + dyp * dyp + dzp * dzp;
    }
}

static void sample_bilinear(const float *px, int64_t w, int64_t h,
                            double u, double v, float out[4])
{
    u -= floor(u);
    v -= floor(v);
    double x = u * (double)(w - 1);
    double y = v * (double)(h - 1);
    int64_t x0 = (int64_t)floor(x);
    int64_t y0 = (int64_t)floor(y);
    int64_t x1 = x0 + 1 < w ? x0 + 1 : w - 1;
    int64_t y1 = y0 + 1 < h ? y0 + 1 : h - 1;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    double tx = x - (double)x0;
    double ty = y - (double)y0;

    const float *c00 = px + (y0 * w + x0) * 4;
    const float *c10 = px + (y0 * w + x1) * 4;
    const float *c01 = px + (y1 * w + x0) * 4;
    const float *c11 = px + (y1 * w + x1) * 4;
    for (int i = 0; i < 4; i++) {
        double a = (double)c00[i] * (1.0 - tx) + (double)c10[i] * tx;
        double b = (double)c01[i] * (1.0 - tx) + (double)c11[i] * tx;
        out[i] = (float)(a * (1.0 - ty) + b * ty);
    }
}

/* Query points are voxel cube centers in source-local space (n_points * 3).
 * tri_uvs: n_tris * 6 floats, bake UV per triangle corner.
 * bake_px: bake_w * bake_h * 4 floats (Blender bottom-up rows, matches UVs).
 * out_rgba: n_points * 4 floats.
 * Returns 0 on success, -1 on bad arguments, -2 on allocation failure. */
int map_colors(const double *verts, int64_t n_verts,
               const int64_t *tris, int64_t n_tris,
               const float *tri_uvs,
               const float *bake_px, int64_t bake_w, int64_t bake_h,
               const double *points, int64_t n_points,
               float *out_rgba)
{
    if (!verts || !tris || !tri_uvs || !bake_px || !points || !out_rgba)
        return -1;
    if (n_verts <= 0 || n_tris <= 0 || n_points <= 0 || bake_w <= 0 || bake_h <= 0)
        return -1;

    /* Mesh bounding box. */
    double bb_min[3] = { verts[0], verts[1], verts[2] };
    double bb_max[3] = { verts[0], verts[1], verts[2] };
    for (int64_t i = 1; i < n_verts; i++) {
        for (int k = 0; k < 3; k++) {
            double v = verts[i * 3 + k];
            if (v < bb_min[k]) bb_min[k] = v;
            if (v > bb_max[k]) bb_max[k] = v;
        }
    }

    double span[3];
    double max_span = 0.0;
    for (int k = 0; k < 3; k++) {
        span[k] = bb_max[k] - bb_min[k];
        if (span[k] > max_span) max_span = span[k];
    }
    if (max_span <= 0.0) max_span = 1.0;

    int64_t n_side = (int64_t)ceil(cbrt((double)n_tris / 2.0));
    if (n_side < 1) n_side = 1;
    if (n_side > 96) n_side = 96;
    double cell = max_span / (double)n_side;
    if (cell <= 0.0) cell = 1.0;

    int64_t gn[3];
    for (int k = 0; k < 3; k++) {
        gn[k] = (int64_t)ceil(span[k] / cell);
        if (gn[k] < 1) gn[k] = 1;
    }
    int64_t n_cells = gn[0] * gn[1] * gn[2];

    int64_t *counts = (int64_t *)calloc((size_t)n_cells + 1, sizeof(int64_t));
    if (!counts)
        return -2;

    /* Count triangle/cell overlaps by triangle bbox. */
    for (int64_t t = 0; t < n_tris; t++) {
        int64_t ia = tris[t * 3 + 0], ib = tris[t * 3 + 1], ic = tris[t * 3 + 2];
        if (ia < 0 || ia >= n_verts || ib < 0 || ib >= n_verts || ic < 0 || ic >= n_verts)
            continue;
        int64_t lo[3], hi[3];
        for (int k = 0; k < 3; k++) {
            double t_min = MIN3(verts[ia * 3 + k], verts[ib * 3 + k], verts[ic * 3 + k]);
            double t_max = MAX3(verts[ia * 3 + k], verts[ib * 3 + k], verts[ic * 3 + k]);
            lo[k] = (int64_t)floor((t_min - bb_min[k]) / cell);
            hi[k] = (int64_t)floor((t_max - bb_min[k]) / cell);
            if (lo[k] < 0) lo[k] = 0;
            if (hi[k] > gn[k] - 1) hi[k] = gn[k] - 1;
        }
        for (int64_t gx = lo[0]; gx <= hi[0]; gx++)
            for (int64_t gy = lo[1]; gy <= hi[1]; gy++)
                for (int64_t gz = lo[2]; gz <= hi[2]; gz++)
                    counts[(gx * gn[1] + gy) * gn[2] + gz + 1]++;
    }
    for (int64_t i = 0; i < n_cells; i++)
        counts[i + 1] += counts[i];

    int64_t total_items = counts[n_cells];
    int64_t *items = (int64_t *)malloc(sizeof(int64_t) * (size_t)(total_items > 0 ? total_items : 1));
    int64_t *cursor = (int64_t *)malloc(sizeof(int64_t) * (size_t)n_cells);
    if (!items || !cursor) {
        free(counts); free(items); free(cursor);
        return -2;
    }
    memcpy(cursor, counts, sizeof(int64_t) * (size_t)n_cells);

    for (int64_t t = 0; t < n_tris; t++) {
        int64_t ia = tris[t * 3 + 0], ib = tris[t * 3 + 1], ic = tris[t * 3 + 2];
        if (ia < 0 || ia >= n_verts || ib < 0 || ib >= n_verts || ic < 0 || ic >= n_verts)
            continue;
        int64_t lo[3], hi[3];
        for (int k = 0; k < 3; k++) {
            double t_min = MIN3(verts[ia * 3 + k], verts[ib * 3 + k], verts[ic * 3 + k]);
            double t_max = MAX3(verts[ia * 3 + k], verts[ib * 3 + k], verts[ic * 3 + k]);
            lo[k] = (int64_t)floor((t_min - bb_min[k]) / cell);
            hi[k] = (int64_t)floor((t_max - bb_min[k]) / cell);
            if (lo[k] < 0) lo[k] = 0;
            if (hi[k] > gn[k] - 1) hi[k] = gn[k] - 1;
        }
        for (int64_t gx = lo[0]; gx <= hi[0]; gx++)
            for (int64_t gy = lo[1]; gy <= hi[1]; gy++)
                for (int64_t gz = lo[2]; gz <= hi[2]; gz++)
                    items[cursor[(gx * gn[1] + gy) * gn[2] + gz]++] = t;
    }

    int64_t max_ring = gn[0] > gn[1] ? gn[0] : gn[1];
    if (gn[2] > max_ring) max_ring = gn[2];

#pragma omp parallel for schedule(dynamic, 256)
    for (int64_t pi = 0; pi < n_points; pi++) {
        const double *p = points + pi * 3;

        int64_t pc[3];
        for (int k = 0; k < 3; k++) {
            pc[k] = (int64_t)floor((p[k] - bb_min[k]) / cell);
            if (pc[k] < 0) pc[k] = 0;
            if (pc[k] > gn[k] - 1) pc[k] = gn[k] - 1;
        }

        double best_d2 = INFINITY;
        int64_t best_tri = -1;
        double best_bary[3] = { 1.0, 0.0, 0.0 };

        for (int64_t r = 0; r <= max_ring; r++) {
            if (best_tri >= 0) {
                double ring_lb = (double)(r - 1) * cell;
                if (ring_lb > 0.0 && best_d2 <= ring_lb * ring_lb)
                    break;
            }
            int any_cell = 0;
            int64_t x0 = pc[0] - r, x1 = pc[0] + r;
            int64_t y0 = pc[1] - r, y1 = pc[1] + r;
            int64_t z0 = pc[2] - r, z1 = pc[2] + r;
            for (int64_t gx = x0; gx <= x1; gx++) {
                if (gx < 0 || gx > gn[0] - 1) continue;
                for (int64_t gy = y0; gy <= y1; gy++) {
                    if (gy < 0 || gy > gn[1] - 1) continue;
                    for (int64_t gz = z0; gz <= z1; gz++) {
                        if (gz < 0 || gz > gn[2] - 1) continue;
                        /* Only the shell of the ring; interior was already scanned. */
                        int on_shell = (gx == x0 || gx == x1 || gy == y0 || gy == y1 || gz == z0 || gz == z1);
                        if (!on_shell) continue;
                        any_cell = 1;
                        int64_t ci = (gx * gn[1] + gy) * gn[2] + gz;
                        for (int64_t it = counts[ci]; it < counts[ci + 1]; it++) {
                            int64_t t = items[it];
                            const double *a = verts + tris[t * 3 + 0] * 3;
                            const double *b = verts + tris[t * 3 + 1] * 3;
                            const double *cc = verts + tris[t * 3 + 2] * 3;
                            double bary[3];
                            double d2 = closest_point_tri(p, a, b, cc, bary);
                            if (d2 < best_d2) {
                                best_d2 = d2;
                                best_tri = t;
                                best_bary[0] = bary[0];
                                best_bary[1] = bary[1];
                                best_bary[2] = bary[2];
                            }
                        }
                    }
                }
            }
            if (!any_cell && r > 0 && best_tri >= 0)
                break;
        }

        float *out = out_rgba + pi * 4;
        if (best_tri < 0) {
            out[0] = out[1] = out[2] = 1.0f;
            out[3] = 1.0f;
            continue;
        }
        const float *uvc = tri_uvs + best_tri * 6;
        double u = (double)uvc[0] * best_bary[0] + (double)uvc[2] * best_bary[1] + (double)uvc[4] * best_bary[2];
        double v = (double)uvc[1] * best_bary[0] + (double)uvc[3] * best_bary[1] + (double)uvc[5] * best_bary[2];
        sample_bilinear(bake_px, bake_w, bake_h, u, v, out);
    }

    free(counts);
    free(items);
    free(cursor);
    return 0;
}
