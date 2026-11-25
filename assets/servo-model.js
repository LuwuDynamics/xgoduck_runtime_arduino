/* Offline, zero-pose CAD viewer. Coordinates in servo-model.json use 0.1 mm. */
class DuckServoModel {
  constructor(canvas, data, onPick) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    if (!this.ctx) throw new Error('Canvas unavailable');
    this.joints = new Map(Object.entries(data.joints).map(([id, joint]) => [Number(id), joint]));
    this.yaw = 0.27;
    this.pitch = 0.14;
    this.selected = null;
    this.faces = [];
    this.vertices = [];
    const palette = {
      servo: [77, 90, 103], lens: [48, 62, 72], beak: [247, 154, 67], foot: [177, 156, 105],
      shell: [223, 231, 233], 'head-cover': [223, 231, 233], frame: [149, 168, 181]
    };
    for (const part of data.parts) {
      const cover = ['lens', 'beak', 'head-cover'].includes(part.surface);
      const base = this.vertices.length;
      for (let i = 0; i < part.vertices.length; i += 3) {
        this.vertices.push(part.vertices.slice(i, i + 3));
      }
      for (let i = 0; i < part.faces.length; i += 3) {
        const [a, b, c] = part.faces.slice(i, i + 3).map(index => base + index);
        const normal = DuckServoModel.normal(this.vertices[a], this.vertices[b], this.vertices[c]);
        this.faces.push({a, b, c, id: part.id, normal, color: palette[part.surface], cover});
      }
    }
    let drag;
    canvas.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0) return;
      canvas.setPointerCapture(event.pointerId);
      drag = {x: event.clientX, y: event.clientY, yaw: this.yaw, pitch: this.pitch, moved: false};
    });
    canvas.addEventListener('pointermove', event => {
      if (!drag || !event.isPrimary) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (Math.hypot(dx, dy) > 5) drag.moved = true;
      if (!drag.moved) return;
      this.yaw = drag.yaw - dx * 0.013;
      this.pitch = Math.max(-0.85, Math.min(0.95, drag.pitch + dy * 0.009));
      this.schedule();
    });
    canvas.addEventListener('pointerup', event => {
      if (!drag || !event.isPrimary) return;
      const rect = canvas.getBoundingClientRect();
      if (!drag.moved) {
        const id = this.pick(event.clientX - rect.left, event.clientY - rect.top);
        if (id != null) onPick(id);
      }
      drag = null;
    });
    canvas.addEventListener('lostpointercapture', () => { drag = null; });
    new ResizeObserver(() => this.schedule()).observe(canvas);
    this.schedule();
  }

  static normal(a, b, c) {
    const [x, y, z] = b.map((v, i) => v - a[i]);
    const [u, v, w] = c.map((n, i) => n - a[i]);
    const cross = [y*w-z*v, z*u-x*w, x*v-y*u];
    const length = Math.hypot(...cross) || 1;
    return cross.map(n => n / length);
  }

  select(id) {
    if (this.selected === id) return;
    const wasInsideHead = this.selected >= 32;
    this.selected = id;
    if (id >= 32 && !wasInsideHead) {
      this.yaw = -0.65;
      this.pitch = 0.55;
    }
    // Keep the selected leg visible even after inspecting the opposite side.
    if (id >= 10 && id <= 24) {
      const side = id < 20 ? 1 : -1;
      if (side * Math.sin(this.yaw) < 0.15) this.yaw = side * 0.42;
      if (wasInsideHead) this.pitch = 0.14;
    }
    this.schedule();
  }

  schedule() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.draw(); });
  }

  draw() {
    const canvas = this.canvas, ctx = this.ctx;
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width || !height) return;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!this.pixels || this.pixels.width !== canvas.width || this.pixels.height !== canvas.height) {
      this.pixels = ctx.createImageData(canvas.width, canvas.height);
      this.rgba = new Uint32Array(this.pixels.data.buffer);
      this.depth = new Float32Array(this.rgba.length);
      this.partIds = new Int16Array(this.rgba.length);
    }
    this.rgba.fill(0);
    this.partIds.fill(-1);
    this.depth.fill(-Infinity);
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const scale = Math.min((height - 23) / 2860, (width - 42) / 2100);
    const project = ([x, y, z]) => {
      x -= 240;
      const forward = x * cy + y * sy;
      return [width / 2 + (-x * sy + y * cy) * scale,
        height / 2 - 7 - (z * cp - forward * sp) * scale, forward * cp + z * sp];
    };
    const screen = this.vertices.map(vertex => {
      const [x,y,z] = project(vertex);
      return [x*dpr, y*dpr, z];
    });
    for (const f of this.faces) {
      if (this.selected >= 32 && f.cover) continue;
      const [x, y, z] = f.normal;
      const forward = x * cy + y * sy;
      const facing = forward * cp + z * sp;
      if (facing <= 0) continue;
      const shade = 0.54 + 0.46 * Math.max(0, 0.60 * facing + 0.67 * (z * cp - forward * sp) - 0.43 * (y * cy - x * sy));
      const selected = f.id !== null && f.id === this.selected;
      const color = selected ? [255, 187, 76] : f.color;
      const [r,g,b] = color.map(n => Math.round(n * shade));
      this.triangle(screen[f.a], screen[f.b], screen[f.c], (255<<24) | (b<<16) | (g<<8) | r, f.id, selected);
    }
    ctx.putImageData(this.pixels,0,0);
    this.markers = [...this.joints].map(([id, joint]) => ({id, point: project(joint.center)}))
      .sort((a,b) => a.point[2] - b.point[2]);
    for (const {id, point: [x,y]} of this.markers) {
      if (id === this.selected) continue;
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fillStyle = '#335a65'; ctx.fill();
      ctx.strokeStyle = '#e1f4ef'; ctx.lineWidth = 1; ctx.stroke();
    }
    const joint = this.joints.get(this.selected);
    if (joint) this.markJoint(joint, project, width);
    canvas.dataset.selected = this.selected ?? '';
  }

  // A depth buffer keeps the internal brackets behind their housings, and records
  // the visible servo ID per pixel so picking still works when two parts overlap.
  triangle(a, b, c, color, id, selected) {
    const {width, height} = this.pixels;
    const x0 = Math.max(0, Math.ceil(Math.min(a[0], b[0], c[0])));
    const x1 = Math.min(width-1, Math.floor(Math.max(a[0], b[0], c[0])));
    const y0 = Math.max(0, Math.ceil(Math.min(a[1], b[1], c[1])));
    const y1 = Math.min(height-1, Math.floor(Math.max(a[1], b[1], c[1])));
    const bx = b[0]-a[0], by = b[1]-a[1], cx = c[0]-a[0], cy = c[1]-a[1];
    const area = bx*cy-by*cx;
    if (!area) return;
    const du = cy/area, dv = -by/area;
    for (let y=y0; y<=y1; y++) {
      let u = ((x0-a[0])*cy-(y-a[1])*cx)/area;
      let v = ((y-a[1])*bx-(x0-a[0])*by)/area;
      for (let x=x0; x<=x1; x++, u+=du, v+=dv) {
        if (u < 0 || v < 0 || u+v > 1) continue;
        // Keep the selected case visible through brackets while preserving its own depth.
        const depth = a[2] + u*(b[2]-a[2]) + v*(c[2]-a[2]) + (selected ? 5000 : 0);
        const pixel = y*width+x;
        if (depth <= this.depth[pixel]) continue;
        this.depth[pixel] = depth;
        this.rgba[pixel] = color;
        this.partIds[pixel] = id ?? -1;
      }
    }
  }

  markJoint({center}, project, width) {
    const ctx = this.ctx, [x,y] = project(center);
    ctx.strokeStyle = '#ffbf51'; ctx.fillStyle = '#ffbf51';
    ctx.beginPath(); ctx.arc(x,y,3,0,2*Math.PI); ctx.fill();
    const side = x >= width/2 ? 1 : -1;
    const labelX = side > 0 ? width-31 : 31, labelY = y-15;
    ctx.lineWidth = 1.3;
    ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(x+side*16,labelY); ctx.lineTo(labelX,labelY); ctx.stroke();
    ctx.fillStyle = '#ffca72'; ctx.fillRect(labelX-24,labelY-10,48,20);
    ctx.fillStyle = '#25190c'; ctx.font = 'bold 12px monospace'; ctx.textAlign = 'center';
    ctx.fillText('ID '+this.selected,labelX,labelY+4);
  }

  pick(x,y) {
    if (!this.markers) return null;
    let nearest = null, distance = 6;
    for (const marker of this.markers) {
      const d = Math.hypot(x-marker.point[0], y-marker.point[1]);
      if (d <= distance) { nearest = marker.id; distance = d; }
    }
    if (nearest !== null) return nearest;
    const px = Math.floor(x*this.pixels.width/this.canvas.clientWidth);
    const py = Math.floor(y*this.pixels.height/this.canvas.clientHeight);
    if (px < 0 || py < 0 || px >= this.pixels.width || py >= this.pixels.height) return null;
    const id = this.partIds[py*this.pixels.width+px];
    return id >= 0 ? id : null;
  }
}
