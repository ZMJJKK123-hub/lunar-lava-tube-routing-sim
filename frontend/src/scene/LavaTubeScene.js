// Three.js 场景 3.0 —— 崎岖地质 + LOS 阻断 + 保姆级反馈
// 噪声位移熔岩管 / 尖锐巨石断层 / 工程感六边形道钉 / 流光管线 / 数据粒子
// Dijkstra 波纹 / 断链闪烁 / 爆炸与冒烟 / Raycaster 拾取聚焦
import * as THREE from 'three'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'

const NODE_COLOR = {
  ACTIVE: 0x35ff9e,
  DEGRADED: 0xffb020,
  SEU_RESET: 0xff7a3c,
  DEAD: 0x3a3f4a,
}

/* ============ 简易 3D 值噪声 (fbm) 用于管壁崎岖位移 ============ */
function hash3(x, y, z) {
  const s = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453
  return s - Math.floor(s)
}
function vnoise(x, y, z) {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z)
  const xf = x - xi, yf = y - yi, zf = z - zi
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf), w = zf * zf * (3 - 2 * zf)
  const lerp = (a, b, t) => a + (b - a) * t
  return lerp(
    lerp(lerp(hash3(xi, yi, zi), hash3(xi + 1, yi, zi), u),
         lerp(hash3(xi, yi + 1, zi), hash3(xi + 1, yi + 1, zi), u), v),
    lerp(lerp(hash3(xi, yi, zi + 1), hash3(xi + 1, yi, zi + 1), u),
         lerp(hash3(xi, yi + 1, zi + 1), hash3(xi + 1, yi + 1, zi + 1), u), v),
    w)
}
function fbm3(x, y, z) {
  return vnoise(x, y, z) * 0.55 + vnoise(x * 2.1, y * 2.1, z * 2.1) * 0.3
       + vnoise(x * 4.3, y * 4.3, z * 4.3) * 0.15
}

export class LavaTubeScene {
  constructor(container, { onNodePick }) {
    this.container = container
    this.onNodePick = onNodePick
    this.nodeMeshes = new Map()
    this.linkMeshes = new Map()
    this.obstacleMeshes = []           // 与 snapshot.obstacles 等长
    this.particles = null
    this.fx = []
    this.snapshot = null
    this.focusTarget = null
    this.selectedId = null
    this.lastEventId = 0
    this.wavePlaying = null
    this.time = 0
    this._smokeClock = {}

    this.initRenderer()
    this.initScene()
    this.initEnvironment()
    this.animate()
  }

  /* ================= 基础设施 ================= */
  initRenderer() {
    this.renderer = new THREE.WebGLRenderer({ antialias: true })
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
    this.renderer.setSize(this.container.clientWidth, this.container.clientHeight)
    this.container.appendChild(this.renderer.domElement)

    this.camera = new THREE.PerspectiveCamera(
      58, this.container.clientWidth / this.container.clientHeight, 0.5, 12000)
    this.camera.position.set(480, 400, 430)
    this.controls = new OrbitControlsLite(this.camera, this.renderer.domElement)
    this.controls.target.set(-30, -110, -640)

    // 严格泛光: 高阈值确保只有高亮主干流光触发锐利 Bloom, 杜绝全屏雾化发光
    this.composer = new EffectComposer(this.renderer)
    this.composer.addPass(new RenderPass(null, this.camera))
    this.bloom = new UnrealBloomPass(
      new THREE.Vector2(innerWidth, innerHeight), 0.5, 0.2, 0.85)
    this.composer.addPass(this.bloom)

    this.raycaster = new THREE.Raycaster()
    this.mouse = new THREE.Vector2()
    this.renderer.domElement.addEventListener('pointerdown', (e) => this.pick(e))
    window.addEventListener('resize', () => this.resize())
  }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h)
    this.composer.setSize(w, h)
  }

  initScene() {
    this.scene = new THREE.Scene()
    this.scene.fog = new THREE.FogExp2(0x030407, 0.00022)
    // 极暗深空: 环境光压到极低, 庞大溶洞大部分隐没黑暗
    this.scene.add(new THREE.AmbientLight(0x223044, 0.28))
    const caveGlow = new THREE.DirectionalLight(0x5577aa, 0.16)
    caveGlow.position.set(30, 80, 80)
    this.scene.add(caveGlow)
    // 探照头灯 (跟随相机, 洞穴探险感)
    this.headlight = new THREE.PointLight(0xd8e6ff, 1.6, 420, 1.3)
    this.scene.add(this.headlight)
    this.composer.passes[0].scene = this.scene
  }

  /* ================= 环境: 月表 / 星空 / 地球 ================= */
  initEnvironment() {
    // ---- 月表 ----
    const groundGeo = new THREE.PlaneGeometry(9000, 9000, 110, 110)
    const pos = groundGeo.attributes.position
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), y = pos.getY(i)
      pos.setZ(i, fbm3(x * 0.002, 0, y * 0.0024) * 130 + Math.sin(x * 0.031) * Math.cos(y * 0.022) * 8)
    }
    groundGeo.computeVertexNormals()
    const ground = new THREE.Mesh(groundGeo,
      new THREE.MeshStandardMaterial({ color: 0x3d3a36, roughness: 1 }))
    ground.rotation.x = -Math.PI / 2
    ground.position.y = -560
    this.scene.add(ground)

    // ---- 洞口信标: 极细半透明光束 (不再是实体圆柱) ----
    const beam = new THREE.Mesh(
      new THREE.CylinderGeometry(1.2, 2.0, 1400, 10, 1, true),
      new THREE.MeshBasicMaterial({
        color: 0x66ccff, transparent: true, opacity: 0.05,
        blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
      }))
    beam.position.set(0, 640, 0)
    this.scene.add(beam)
    const mouthLight = new THREE.PointLight(0x88bbff, 0.9, 260, 1.5)
    mouthLight.position.set(0, 20, 40)
    this.scene.add(mouthLight)

    // ---- 星空 + 地球 ----
    const starGeo = new THREE.BufferGeometry()
    const sp = new Float32Array(3600 * 3)
    for (let i = 0; i < 3600; i++) {
      const v = new THREE.Vector3().randomDirection().multiplyScalar(5200)
      sp.set([v.x, Math.abs(v.y) * 0.7 + 150, v.z], i * 3)
    }
    starGeo.setAttribute('position', new THREE.BufferAttribute(sp, 3))
    this.scene.add(new THREE.Points(starGeo,
      new THREE.PointsMaterial({ color: 0xaaccff, size: 1.6, sizeAttenuation: false })))

    const earth = new THREE.Mesh(
      new THREE.SphereGeometry(150, 32, 32),
      new THREE.MeshBasicMaterial({ color: 0x2a66d9 }))
    earth.position.set(-1500, 1100, -2400)
    this.scene.add(earth)
    const earthGlow = new THREE.Mesh(
      new THREE.SphereGeometry(165, 32, 32),
      new THREE.MeshBasicMaterial({ color: 0x66aaff, transparent: true, opacity: 0.18, blending: THREE.AdditiveBlending }))
    earthGlow.position.copy(earth.position)
    this.scene.add(earthGlow)
  }

  /* Fresnel 全息壳: 只有斜视角的边缘发亮, 正对时近乎透明。
   * 管道轮廓在任何背景下都清晰可辨, 又完全不遮挡内部信号线。 */
  _holoShellMaterial(color = 0x3d84c9, intensity = 0.5) {
    return new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(color) },
                  uInt: { value: intensity } },
      vertexShader: `
        varying vec3 vN; varying vec3 vV;
        void main() {
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          vN = normalize(normalMatrix * normal);
          vV = normalize(-mv.xyz);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        uniform vec3 uColor; uniform float uInt;
        varying vec3 vN; varying vec3 vV;
        void main() {
          float fres = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 3.0);
          gl_FragColor = vec4(uColor * fres * uInt, 1.0);   // 加色描边, 中心无贡献
        }`,
      transparent: true, depthWrite: false, side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
    })
  }

  /* ================= 多分支迷宫地质 (按后端 geology 构建) ================= */
  buildGeology(geo) {
    if (this._geologyBuilt) return
    this._geologyBuilt = true
    // 极暗岩石: 高粗糙几乎不反光, 低金属度
    const rockMat = new THREE.MeshStandardMaterial({
      color: 0x1a1a20, roughness: 0.9, metalness: 0.1,
      flatShading: true, side: THREE.BackSide,   // 从洞穴内部观看
    })

    // 1. 隧道: 多条曲线交汇的管网 (主干/环路/死胡同)
    for (const tu of geo.tunnels ?? []) {
      const curve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(...tu.a), new THREE.Vector3(...tu.mid), new THREE.Vector3(...tu.b),
      ])
      const tubeGeo = new THREE.TubeGeometry(curve, 90, tu.r, 20, false)
      this._displaceRock(tubeGeo, tu.r * 0.38, 0.016)
      this.scene.add(new THREE.Mesh(tubeGeo, rockMat))
      // 全息轮廓壳: 边缘发亮勾勒管道走向, 不遮挡内部信号线
      const shell = new THREE.Mesh(
        new THREE.TubeGeometry(curve, 60, tu.r + 2.5, 14, false),
        this._holoShellMaterial(0x3d84c9, 0.3))
      shell.renderOrder = 2
      this.scene.add(shell)
    }

    // 2. 腔室: 交汇处的不规则大岩洞 (高细分 icosahedron + 强噪声位移)
    for (const c of geo.chambers ?? []) {
      const geoS = new THREE.IcosahedronGeometry(c.r, 3)
      this._displaceRock(geoS, c.r * 0.24, 0.011)
      this.scene.add(new THREE.Mesh(geoS, rockMat))
      // 腔室全息轮廓壳
      const cshell = new THREE.Mesh(
        new THREE.IcosahedronGeometry(c.r + 2.5, 2),
        this._holoShellMaterial(0x3d84c9, 0.18))
      cshell.renderOrder = 2
      this.scene.add(cshell)
      // 腔室微弱环境光 (幽深洞穴中的一丝冷光)
      const cl = new THREE.PointLight(0x9db8e8, 0.5, c.r * 3.6, 1.5)
      cl.position.set(c.x, c.y + c.r * 0.35, c.z)
      this.scene.add(cl)
    }

    // 3. 石柱: 腔室中央连接天地的巨柱 (噪声扭曲, 严苛物理遮挡)
    for (const p of geo.pillars ?? []) {
      const cyl = new THREE.CylinderGeometry(p.r * 0.68, p.r, p.h, 10, 8, false)
      this._displaceRockRadial(cyl, p.r * 0.34, 0.014)
      const pillar = new THREE.Mesh(cyl, new THREE.MeshStandardMaterial({
        color: 0x1e1926, roughness: 0.95, metalness: 0.08, flatShading: true,
      }))
      pillar.position.set(p.x, p.y, p.z)
      this.scene.add(pillar)
      // 柱底碎石堆
      for (let i = 0; i < 6; i++) {
        const rr = p.r * (1.1 + Math.random() * 0.5)
        const a = Math.random() * Math.PI * 2
        const rock = new THREE.Mesh(
          new THREE.DodecahedronGeometry(p.r * 0.35 + Math.random() * 1.5, 0),
          new THREE.MeshStandardMaterial({ color: 0x1c1722, roughness: 1, flatShading: true }))
        rock.position.set(p.x + Math.cos(a) * rr, p.y - p.h * 0.42, p.z + Math.sin(a) * rr)
        rock.rotation.set(Math.random() * 3, Math.random() * 3, 0)
        this.scene.add(rock)
      }
    }
  }

  _displaceRockRadial(geo, amp, freq) {
    // 圆柱专用: 沿径向扭曲 (保持顶底不动, 中段大幅摇晃 -> 天然石柱)
    const p = geo.attributes.position
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), y = p.getY(i), z = p.getZ(i)
      const h = Math.abs(y) / (geo.parameters.height / 2)
      const wob = (fbm3(x * freq + 7.1, y * freq * 0.6, z * freq + 3.3) - 0.5) * 2
      const bulge = 1 + wob * (amp / Math.max(1, Math.hypot(x, z))) * (1 - h * h)
      p.setXYZ(i, x * bulge, y, z * bulge)
    }
    geo.computeVertexNormals()
  }

  _displaceRock(geo, amp, freq) {
    // 沿法线做 fbm 位移, 再叠加尖锐脊线 -> 崎岖不平的岩壁
    const p = geo.attributes.position
    const n = geo.attributes.normal
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), y = p.getY(i), z = p.getZ(i)
      let d = (fbm3(x * freq + 31.7, y * freq + 11.3, z * freq + 7.9) - 0.5) * 2.0
      d += (Math.abs(fbm3(x * freq * 2.7, y * freq * 2.7, z * freq * 2.7) - 0.5) - 0.22) * 1.6
      p.setXYZ(i, x + n.getX(i) * d * amp, y + n.getY(i) * d * amp, z + n.getZ(i) * d * amp)
    }
    geo.computeVertexNormals()
  }

  /* ================= 巨石 / 断层 (按后端 obstacles 渲染) ================= */
  syncObstacles(obstacles) {
    // 增量生成: 与 snapshot.obstacles 等长对齐
    for (let i = this.obstacleMeshes.length; i < obstacles.length; i++) {
      const o = obstacles[i]
      let mesh
      if (o.shape === 'spike') {
        mesh = new THREE.Mesh(
          new THREE.ConeGeometry(o.r, o.h + o.r, 5, 1),
          new THREE.MeshStandardMaterial({ color: 0x2a2230, roughness: 1, flatShading: true })
        )
      } else if (o.shape === 'slab') {
        mesh = new THREE.Mesh(
          new THREE.BoxGeometry(o.r * 2.2, o.h, o.r * 1.3),
          new THREE.MeshStandardMaterial({ color: 0x241d28, roughness: 1, flatShading: true }))
        mesh.rotation.set(0.4, (o.rot * Math.PI) / 180, -0.3)
      } else if (o.shape === 'boulder') {
        // 塌方新落巨石: 带炽热余温
        mesh = new THREE.Mesh(
          new THREE.IcosahedronGeometry(o.r, 1),
          new THREE.MeshStandardMaterial({
            color: 0x33221c, roughness: 0.9, flatShading: true,
            emissive: 0x661808, emissiveIntensity: 0.9,
          }))
      } else { // shard
        const g = new THREE.DodecahedronGeometry(o.r, 0)
        const pp = g.attributes.position
        for (let k = 0; k < pp.count; k++) {
          const f = 0.72 + hash3(pp.getX(k), pp.getY(k), pp.getZ(k)) * 0.62
          pp.setXYZ(k, pp.getX(k) * f, pp.getY(k) * f * 1.3, pp.getZ(k) * f)
        }
        g.computeVertexNormals()
        mesh = new THREE.Mesh(g,
          new THREE.MeshStandardMaterial({ color: 0x271f2e, roughness: 1, flatShading: true }))
      }
      mesh.position.set(o.x, o.y, o.z)
      mesh.rotation.y += (o.rot * Math.PI) / 180
      if (o.shape === 'spike') mesh.rotation.x = 0.18
      this.scene.add(mesh)
      this.obstacleMeshes.push(mesh)
      if (o.shape === 'boulder') this.spawnDust(mesh.position)  // 塌方扬尘
    }
  }

  /* ================= 通信桩 (工程感六边形道钉) ================= */
  buildNodeMesh(n) {
    const g = new THREE.Group()
    const metal = (c, r = 0.5, m = 0.75) =>
      new THREE.MeshStandardMaterial({ color: c, roughness: r, metalness: m })

    // 六边形锚固底座 + 螺栓桩
    const base = new THREE.Mesh(new THREE.CylinderGeometry(2.5, 2.9, 1.0, 6), metal(0x59606e, 0.6))
    base.position.y = 0.5
    const skirt = new THREE.Mesh(new THREE.CylinderGeometry(3.3, 3.3, 0.35, 6), metal(0x3d434e, 0.8, 0.5))
    skirt.position.y = 0.15
    // 六棱支柱
    const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.75, 5.4, 6), metal(0x9aa7b8, 0.4))
    mast.position.y = 3.4
    mast.name = 'spike'
    // 散热环 x2
    const finMat = metal(0x7f8b9c, 0.35)
    const fin1 = new THREE.Mesh(new THREE.TorusGeometry(1.05, 0.16, 6, 12), finMat)
    fin1.rotation.x = Math.PI / 2; fin1.position.y = 3.1
    const fin2 = fin1.clone(); fin2.position.y = 4.3
    // 顶部设备舱
    const pod = new THREE.Mesh(new THREE.BoxGeometry(1.7, 1.05, 1.7), metal(0xc9d4e2, 0.3))
    pod.position.y = 6.3
    // 太阳能板
    const panel = new THREE.Mesh(
      new THREE.BoxGeometry(3.8, 0.12, 2.3),
      new THREE.MeshStandardMaterial({ color: 0x11335e, roughness: 0.2, metalness: 0.9 }))
    panel.position.set(0, 6.0, 1.8)
    panel.rotation.x = -0.5
    // 天线杆 + 十字振子 (倾角实时驱动)
    const antGroup = new THREE.Group()
    antGroup.position.y = 6.9
    const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 3.4),
      new THREE.MeshBasicMaterial({ color: 0xd0e8ff }))
    rod.position.y = 1.7
    const cross = new THREE.Mesh(new THREE.BoxGeometry(2.8, 0.14, 0.14),
      new THREE.MeshBasicMaterial({ color: 0x9fd8ff }))
    cross.position.y = 3.2
    antGroup.add(rod, cross)
    antGroup.name = 'antenna'
    // 状态指示灯 + 常驻光晕 (吃 bloom)
    const lamp = new THREE.Mesh(new THREE.SphereGeometry(1.15, 12, 12),
      new THREE.MeshBasicMaterial({ color: 0x35ff9e }))
    lamp.position.y = 8.3
    lamp.name = 'lamp'
    const halo = new THREE.Mesh(new THREE.SphereGeometry(2.5, 12, 12),
      new THREE.MeshBasicMaterial({
        color: 0x35ff9e, transparent: true, opacity: 0.2,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }))
    halo.position.y = 8.3
    halo.name = 'halo'

    g.add(base, skirt, mast, fin1, fin2, pod, panel, antGroup, lamp, halo)
    // 拾取热区: 不可见的大球 (模型缩小后保证 3D 点选体验)
    const pickZone = new THREE.Mesh(
      new THREE.SphereGeometry(14, 8, 8),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }))
    pickZone.position.y = 4
    pickZone.name = 'pickzone'
    g.add(pickZone)
    // 节点整体缩小: 宏大溶洞中的微小点缀
    g.scale.setScalar(0.35)
    g.userData = { id: n.id }
    return g
  }

  _ensureNodePoints() {
    if (this.nodePoints) return
    const MAX = 128
    const pos = new Float32Array(MAX * 3)
    const col = new Float32Array(MAX * 3)
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3))
    const pts = new THREE.Points(geo, new THREE.PointsMaterial({
      size: 15, vertexColors: true, transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }))
    this.scene.add(pts)
    this.nodePoints = { pts, pos, col, geo }
  }

  syncNodes(nodes) {
    for (const [id, n] of Object.entries(nodes)) {
      let g = this.nodeMeshes.get(id)
      if (!g) {
        g = this.buildNodeMesh(n)
        g.position.set(n.x, n.y, n.z)
        this.scene.add(g)
        this.nodeMeshes.set(id, g)
      }
      const lamp = g.getObjectByName('lamp')
      const halo = g.getObjectByName('halo')
      const color = NODE_COLOR[n.state] ?? 0x35ff9e
      lamp.material.color.setHex(color)
      halo.material.color.setHex(color)
      const stress = Math.min(1, (100 - (n.battery_soc ?? 100)) / 100 + (n.queue_pct ?? 0) / 180)
      lamp.scale.setScalar(1 + Math.sin(this.time * (4 + stress * 8)) * 0.22 * (0.5 + stress))
      halo.material.opacity = n.state === 'DEAD' ? 0.04 : 0.16 + stress * 0.22
      const ant = g.getObjectByName('antenna')
      ant.rotation.z = THREE.MathUtils.degToRad(n.tilt_deg ?? 0)
      g.rotation.x = n.state === 'DEAD' ? Math.PI * 0.42 : 0
      // 高温烧红 / 深冷结霜
      const mast = g.getObjectByName('spike')
      const t = n.temp_c ?? 0
      mast.material.emissive.setHex(t > 45 ? 0x7a1408 : t < -45 ? 0x081c3a : 0x000000)
      mast.material.emissiveIntensity = t > 45 ? Math.min(1.5, (t - 45) / 45) : 0
    }
    // 光点层: 每个节点一颗加色光点 (远处可见, 吃 bloom)
    this._ensureNodePoints()
    const { pos, col, geo } = this.nodePoints
    const c = new THREE.Color()
    let i = 0
    for (const n of Object.values(nodes)) {
      if (i >= 128) break
      pos.set([n.x, n.y + 2, n.z], i * 3)
      c.setHex(NODE_COLOR[n.state] ?? 0x35ff9e)
      const boost = n.state === 'DEAD' ? 0.35 : 1.35
      col.set([c.r * boost, c.g * boost, c.b * boost], i * 3)
      i++
    }
    geo.setDrawRange(0, i)
    geo.attributes.position.needsUpdate = true
    geo.attributes.color.needsUpdate = true
  }

  /* ================= 链路: 两级视觉分级 =================
   * 激活主干 (被路由选中、正在传数据): 青绿流光管线, 流动亮段超阈值触发锐利 Bloom
   * 潜在链路 (物理可达但未被选中): 深墨绿细线 (LineBasicMaterial), 绝不发光
   * 熔断链路: 暗红细线
   */
  linkKey(a, b) { return [a, b].sort().join('|') }

  _flowMaterial(colorHSL) {
    const mat = new THREE.MeshBasicMaterial({
      transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false,
    })
    mat.defines = { USE_UV: '' }
    mat.color.setHSL(colorHSL.h, colorHSL.s, colorHSL.l)
    mat.onBeforeCompile = (shader) => {
      shader.uniforms.uTime = { value: 0 }
      shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', '#include <common>\nuniform float uTime;')
        .replace('#include <dithering_fragment>', `
          float stripe = pow(abs(sin((vUv.x * 6.0 - uTime * 0.5) * 3.14159)), 2.5);
          gl_FragColor.rgb *= 0.5 + 2.4 * stripe;
          #include <dithering_fragment>`)
      mat.userData.shader = shader
    }
    return mat
  }

  _makeLinkCurve(ga, gb) {
    const pa = ga.position.clone().add(new THREE.Vector3(0, 3.2, 0))
    const pb = gb.position.clone().add(new THREE.Vector3(0, 3.2, 0))
    const mid = pa.clone().add(pb).multiplyScalar(0.5); mid.y += 18
    return new THREE.QuadraticBezierCurve3(pa, mid, pb)
  }

  _disposeLink(entry) {
    this.scene.remove(entry.mesh)
    entry.mesh.geometry?.dispose?.()
    entry.mat?.dispose?.()
  }

  syncLinks(links) {
    const alive = new Set()
    for (const lk of links) {
      const ga = this.nodeMeshes.get(lk.a), gb = this.nodeMeshes.get(lk.b)
      if (!ga || !gb) continue
      const key = this.linkKey(lk.a, lk.b)
      alive.add(key)
      const active = this.activeEdges?.has(key) && lk.up
      const wantMode = active ? 'tube' : 'line'

      let entry = this.linkMeshes.get(key)
      if (!entry) {
        entry = { mode: null, mesh: null, mat: null, fade: 0 }
        this.linkMeshes.set(key, entry)
      }
      // 状态切换 -> 重建对象
      if (entry.mode !== wantMode) {
        if (entry.mesh) this._disposeLink(entry)
        if (wantMode === 'tube') {
          const hsl = { h: 0.44, s: 1.0, l: 0.6 }   // 青绿 #00FFCC 系
          entry.mat = this._flowMaterial(hsl)
          entry.mesh = new THREE.Mesh(
            new THREE.TubeGeometry(this._makeLinkCurve(ga, gb), 24, 2.1, 6, false), entry.mat)
          entry.targetOpacity = 0.95
        } else {
          const dead = !lk.up
          entry.mat = new THREE.LineBasicMaterial({
            color: dead ? 0x331111 : 0x113311,   // 熔断暗红 / 潜在深墨绿
            transparent: true, opacity: 0, depthWrite: false,
          })
          const pts = this._makeLinkCurve(ga, gb).getPoints(14)
          entry.mesh = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(pts), entry.mat)
          entry.targetOpacity = dead ? 0.12 : 0.2
        }
        entry.mode = wantMode
        this.scene.add(entry.mesh)
      } else if (entry.mode === 'tube') {
        // 拓扑变化后更新几何 (节点倒伏等)
        entry.mesh.geometry.dispose()
        entry.mesh.geometry = new THREE.TubeGeometry(this._makeLinkCurve(ga, gb), 24, 2.1, 6, false)
        entry.targetOpacity = 0.6 + Math.min(0.35, (lk.load ?? 0) * 0.06)
      }
      entry.up = lk.up
      entry.snrDb = lk.snr_db
    }
    // 消失的链路 -> 渐隐移除
    for (const [key, entry] of this.linkMeshes) {
      if (!alive.has(key) && entry.mode !== null) {
        entry.targetOpacity = 0
        entry.dying = true
      }
    }
  }

  updateLinks(dt) {
    for (const [key, entry] of this.linkMeshes) {
      if (!entry.mesh) { this.linkMeshes.delete(key); continue }
      entry.fade = THREE.MathUtils.lerp(entry.fade, entry.targetOpacity ?? 0, dt * 5)
      const pulse = entry.mode === 'tube' ? 1 + Math.sin(this.time * 5) * 0.18 : 1
      entry.mat.opacity = entry.fade * pulse
      const sh = entry.mat.userData?.shader
      if (sh) sh.uniforms.uTime.value = this.time
      if (entry.dying && entry.fade < 0.02) {
        this._disposeLink(entry)
        this.linkMeshes.delete(key)
      }
    }
  }

  /* ================= 数据粒子流 ================= */
  buildParticles(traffic) {
    const MAX = 5000
    if (!this.particles) {
      const positions = new Float32Array(MAX * 3)
      const colors = new Float32Array(MAX * 3)
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
      geo.setAttribute('color', new THREE.BufferAttribute(colors, 3))
      const points = new THREE.Points(geo,
        new THREE.PointsMaterial({ size: 13, vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }))
      this.scene.add(points)
      this.particles = { points, flows: [], positions, colors, geo }
    }
    const { flows } = this.particles
    flows.length = 0
    let count = 0
    const selPath = this.selectedPath
    for (const tr of traffic ?? []) {
      const path = (tr.path ?? []).map((id) => this.nodeMeshes.get(id)).filter(Boolean)
      if (path.length < 2) continue
      const wp = path.map((g) => g.position.clone().add(new THREE.Vector3(0, 3.2, 0)))
      let slow = 1, hue = 0.44
      for (let i = 0; i < tr.path.length - 1; i++) {
        const e = this.linkMeshes.get(this.linkKey(tr.path[i], tr.path[i + 1]))
        if (e) {
          const q = THREE.MathUtils.clamp(((e.snrDb ?? 12) + 4) / 26, 0.06, 1)
          slow = Math.min(slow, q)
          hue = Math.min(hue, Math.max(0.0, q * 0.44))
        }
      }
      const selected = selPath && tr.path.some((id) => selPath.includes(id))
      const n = selected ? 10 : 4
      for (let i = 0; i < n && count < MAX; i++) {
        flows.push({ wp, t: i / n, speed: 0.0018 * slow, hue: selected ? 0.12 : hue })
        count++
      }
    }
  }

  updateParticles(dt) {
    if (!this.particles) return
    const { flows, positions, colors, geo } = this.particles
    const tmp = new THREE.Vector3()
    const col = new THREE.Color()
    flows.forEach((f, i) => {
      f.t = (f.t + f.speed * dt * 60) % 1
      const seg = f.wp.length - 1
      const p = f.t * seg
      const i0 = Math.floor(p), i1 = Math.min(i0 + 1, f.wp.length - 1)
      tmp.lerpVectors(f.wp[i0], f.wp[i1], p - i0)
      positions.set([tmp.x, tmp.y, tmp.z], i * 3)
      col.setHSL(f.hue, 0.95, 0.62)
      colors.set([col.r, col.g, col.b], i * 3)
    })
    geo.setDrawRange(0, flows.length)
    geo.attributes.position.needsUpdate = true
    geo.attributes.color.needsUpdate = true
  }

  /* ================= 算法过程可视化: 波前扩散 + 事件动画 ================= */
  playWave(wave) {
    if (!wave?.settle_order?.length) return
    this.wavePlaying = { order: wave.settle_order, idx: 0, next: 0, hopOf: wave.hop_of ?? {} }
  }

  spawnRipple(nodeId, hop) {
    const g = this.nodeMeshes.get(nodeId)
    if (!g) return
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(1, 1.5, 40),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color().setHSL(0.55 - Math.min(hop, 8) * 0.05, 0.9, 0.6),
        transparent: true, opacity: 0.9, side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }))
    ring.rotation.x = -Math.PI / 2
    ring.position.copy(g.position).add(new THREE.Vector3(0, 0.5, 0))
    this.scene.add(ring)
    this.fx.push({
      obj: ring, life: 0, ttl: 1.1,
      tick: (o, k) => { o.scale.setScalar(1 + k * 60); o.material.opacity = 0.9 * (1 - k) },
    })
    const lamp = g.getObjectByName('lamp')
    if (lamp) {
      const halo2 = new THREE.Mesh(
        new THREE.SphereGeometry(10, 12, 12),
        new THREE.MeshBasicMaterial({ color: 0x66ddff, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending }))
      halo2.position.copy(lamp.getWorldPosition(new THREE.Vector3()))
      this.scene.add(halo2)
      this.fx.push({
        obj: halo2, life: 0, ttl: 0.6,
        tick: (o, k) => { o.material.opacity = 0.5 * (1 - k); o.scale.setScalar(1 + k * 0.8) },
      })
    }
  }

  spawnBlast(nodeId) {
    const g = this.nodeMeshes.get(nodeId)
    if (!g) return
    const ball = new THREE.Mesh(
      new THREE.SphereGeometry(9, 20, 20),
      new THREE.MeshBasicMaterial({ color: 0xff5a2a, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending }))
    ball.position.copy(g.position)
    this.scene.add(ball)
    this.fx.push({
      obj: ball, life: 0, ttl: 1.6,
      tick: (o, k) => { o.scale.setScalar(1 + k * 70); o.material.opacity = 0.95 * Math.pow(1 - k, 2) },
    })
    this.spawnSmoke(nodeId, 14)
  }

  spawnSmoke(nodeId, n = 6) {
    const g = this.nodeMeshes.get(nodeId)
    if (!g) return
    const origin = g.position.clone().add(new THREE.Vector3(0, 3.5, 0))
    for (let i = 0; i < n; i++) {
      const p = new THREE.Mesh(
        new THREE.SphereGeometry(2.6 + Math.random() * 2.4, 8, 8),
        new THREE.MeshBasicMaterial({ color: 0x8a8f98, transparent: true, opacity: 0.55, depthWrite: false }))
      p.position.copy(origin).add(new THREE.Vector3(
        (Math.random() - 0.5) * 5, Math.random() * 4, (Math.random() - 0.5) * 5))
      const vy = 9 + Math.random() * 10
      const vx = (Math.random() - 0.5) * 1.1, vz = (Math.random() - 0.5) * 1.1
      this.scene.add(p)
      this.fx.push({
        obj: p, life: 0, ttl: 2.2,
        tick: (o, k, dt) => {
          o.position.x += vx * dt; o.position.y += vy * dt; o.position.z += vz * dt
          o.scale.setScalar(1 + k * 4.5)
          o.material.opacity = 0.55 * (1 - k)
        },
      })
    }
  }

  spawnDust(pos) {
    // 塌方扬尘
    for (let i = 0; i < 10; i++) {
      const p = new THREE.Mesh(
        new THREE.SphereGeometry(2.4, 8, 8),
        new THREE.MeshBasicMaterial({ color: 0xa88a6a, transparent: true, opacity: 0.5, depthWrite: false }))
      p.position.copy(pos)
      const vx = (Math.random() - 0.5) * 22, vz = (Math.random() - 0.5) * 22
      this.scene.add(p)
      this.fx.push({
        obj: p, life: 0, ttl: 1.8,
        tick: (o, k, dt) => {
          o.position.x += vx * dt; o.position.z += vz * dt; o.position.y += 5 * dt
          o.scale.setScalar(1 + k * 7); o.material.opacity = 0.5 * (1 - k)
        },
      })
    }
  }

  flashLink(aId, bId, color) {
    const e = this.linkMeshes.get(this.linkKey(aId, bId))
    if (!e) return
    const flash = e.mesh.clone()
    flash.material = new THREE.MeshBasicMaterial({
      color, transparent: true, opacity: 1, blending: THREE.AdditiveBlending, depthWrite: false })
    this.scene.add(flash)
    this.fx.push({
      obj: flash, life: 0, ttl: 1.0,
      tick: (o, k) => { o.material.opacity = (1 - k) * (0.5 + 0.5 * Math.sin(k * 40)) },
    })
  }

  processEvents(events) {
    for (const ev of events ?? []) {
      if (ev.id <= this.lastEventId) continue
      this.lastEventId = ev.id
      switch (ev.type) {
        case 'link_down': this.flashLink(ev.a, ev.b, 0xff2222); break
        case 'link_up': this.flashLink(ev.a, ev.b, 0x22ff88); break
        case 'node_dead': this.spawnBlast(ev.node); break
        case 'healing_start':
          if (this.snapshot?.wave) this.playWave(this.snapshot.wave)
          break
        case 'reroute':
          this.spawnRipple(ev.node, (ev.new_path ?? []).length - 1)
          break
      }
    }
  }

  updateWave(dt) {
    const w = this.wavePlaying
    if (!w) return
    w.next -= dt
    while (w.next <= 0 && w.idx < w.order.length) {
      const nid = w.order[w.idx++]
      this.spawnRipple(nid, w.hopOf[nid] ?? 0)
      w.next += 0.085
    }
    if (w.idx >= w.order.length) this.wavePlaying = null
  }

  /* ================= 巡检机器人: 动态移动信源 (RCSPA 实时重规划) ================= */
  syncRobot(robot) {
    if (!robot) return
    if (!this.robotMesh) {
      const g = new THREE.Group()
      const core = new THREE.Mesh(
        new THREE.SphereGeometry(6, 16, 16),
        new THREE.MeshBasicMaterial({ color: 0xdfffef }))
      const glow = new THREE.Mesh(
        new THREE.SphereGeometry(13, 16, 16),
        new THREE.MeshBasicMaterial({
          color: 0x66ffd9, transparent: true, opacity: 0.32,
          blending: THREE.AdditiveBlending, depthWrite: false }))
      const light = new THREE.PointLight(0x88ffe0, 1.3, 170, 1.4)
      g.add(core, glow, light)
      g.position.set(robot.x, robot.y, robot.z)
      this.scene.add(g)
      this.robotMesh = g
      // 机器人 RCSPA 路径: 金色流光管线
      const mat = this._flowMaterial({ h: 0.12, s: 1.0, l: 0.62 })
      this.robotPathMesh = new THREE.Mesh(new THREE.BufferGeometry(), mat)
      this.scene.add(this.robotPathMesh)
      // 信号脉冲: 3 个白金大光点沿路径缓慢流向洞口
      this.robotPulses = [{ t: 0 }, { t: 0.33 }, { t: 0.66 }]
      this.robotPulseMeshes = this.robotPulses.map(() => {
        const m = new THREE.Mesh(
          new THREE.SphereGeometry(4.5, 10, 10),
          new THREE.MeshBasicMaterial({ color: 0xfff0c0, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending }))
        this.scene.add(m)
        return m
      })
    }
    // 平滑追踪后端位置 (插值移动)
    this.robotTarget = new THREE.Vector3(robot.x, robot.y, robot.z)
    // 重建路径管线: 机器人当前位置 -> 前方节点 -> ... -> 洞口
    const path = robot.route?.path ?? []
    if (path.length >= 1) {
      const pts = [new THREE.Vector3(robot.x, robot.y, robot.z)]
      for (const nid of path) {
        const g = this.nodeMeshes.get(nid)
        if (g) pts.push(g.position.clone().add(new THREE.Vector3(0, 3.2, 0)))
      }
      this.robotWaypoints = pts
      const curve = new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0.08)
      this.robotPathMesh.geometry.dispose()
      this.robotPathMesh.geometry = new THREE.TubeGeometry(curve, Math.max(24, pts.length * 8), 2.2, 6, false)
      this.robotPathMesh.material.opacity = 0.85
    }
  }

  updateRobot(dt) {
    if (this.robotMesh && this.robotTarget) {
      this.robotMesh.position.lerp(this.robotTarget, 0.06)
      this.robotMesh.children[1].scale.setScalar(1 + Math.sin(this.time * 3) * 0.12)
    }
    // 脉冲沿路径缓慢传递 (平稳节奏)
    if (this.robotWaypoints?.length > 1 && this.robotPulseMeshes) {
      const wp = this.robotWaypoints
      this.robotPulses.forEach((p, i) => {
        p.t = (p.t + dt * 0.028) % 1
        const seg = wp.length - 1
        const q = p.t * seg
        const i0 = Math.floor(q), i1 = Math.min(i0 + 1, wp.length - 1)
        this.robotPulseMeshes[i].position.lerpVectors(wp[i0], wp[i1], q - i0)
      })
    }
  }

  /* ================= Raycaster 拾取 + 相机聚焦 ================= */
  pick(e) {
    const rect = this.renderer.domElement.getBoundingClientRect()
    this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
    this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    this.raycaster.setFromCamera(this.mouse, this.camera)
    const hits = this.raycaster.intersectObjects([...this.nodeMeshes.values()], true)
    if (hits.length) {
      let obj = hits[0].object
      while (obj && !obj.userData?.id) obj = obj.parent
      if (obj?.userData?.id) {
        this.focusTarget = obj.position.clone().add(new THREE.Vector3(0, 4, 0))
        this.onNodePick(obj.userData.id)
      }
    }
  }

  select(id) {
    this.selectedId = id
    const path = this.snapshot?.routes?.[id]?.path
    this.selectedPath = path?.length ? path : null
    this._showBlocked(id)
  }

  /* 视距架构呈现: 红色断裂虚线 = 距离很近但视线被岩壁/巨石挡住 (图中无边) */
  _showBlocked(id) {
    if (this.blockGroup) {
      this.scene.remove(this.blockGroup)
      this.blockGroup.traverse((o) => { o.geometry?.dispose?.(); o.material?.dispose?.() })
      this.blockGroup = null
    }
    if (!id) return
    const g1 = this.nodeMeshes.get(id)
    const info = this.snapshot?.nodes?.[id]?.blocked_nbrs ?? []
    if (!g1 || !info.length) return
    const grp = new THREE.Group()
    for (const b of info) {
      const g2 = this.nodeMeshes.get(b.id)
      if (!g2) continue
      const pa = g1.position.clone().add(new THREE.Vector3(0, 2.5, 0))
      const pb = g2.position.clone().add(new THREE.Vector3(0, 2.5, 0))
      const geo = new THREE.BufferGeometry().setFromPoints([pa, pb])
      const line = new THREE.Line(geo, new THREE.LineDashedMaterial({
        color: 0xcc4444, dashSize: 9, gapSize: 7, transparent: true, opacity: 0.8,
      }))
      line.computeLineDistances()
      grp.add(line)
      // 中点 X 标记: 视线在此被岩壁掐断
      const mid = pa.clone().add(pb).multiplyScalar(0.5)
      const xmat = new THREE.MeshBasicMaterial({ color: 0xff5544 })
      const bar1 = new THREE.Mesh(new THREE.BoxGeometry(9, 1.6, 1.6), xmat)
      const bar2 = bar1.clone(); bar2.rotation.y = Math.PI / 2
      bar1.lookAt(pb); bar2.lookAt(pb)
      bar1.position.copy(mid); bar2.position.copy(mid)
      grp.add(bar1, bar2)
    }
    this.scene.add(grp)
    this.blockGroup = grp
  }

  /* ================= 主循环 ================= */
  update(snapshot) {
    // 激活主干 = 所有正在传数据的路径上的相邻节点对 (算法选中的边)
    const active = new Set()
    for (const tr of snapshot.traffic ?? []) {
      const p = tr.path ?? []
      for (let i = 0; i < p.length - 1; i++) active.add(this.linkKey(p[i], p[i + 1]))
    }
    this.activeEdges = active
    for (const lk of snapshot.links ?? []) {
      const e = this.linkMeshes.get(this.linkKey(lk.a, lk.b))
      if (e) e.snrDb = lk.snr_db
    }
    this.snapshot = snapshot
    this.syncObstacles(snapshot.obstacles)
    this.syncNodes(snapshot.nodes)
    this.syncLinks(snapshot.links)
    this.buildParticles(snapshot.traffic)
    this.syncRobot(snapshot.robot)
    this.processEvents(snapshot.events)
    if (this.selectedId) this.select(this.selectedId)
  }

  animate = () => {
    requestAnimationFrame(this.animate)
    this.time += 0.016
    const dt = 0.016

    if (this.focusTarget) {
      const camTarget = this.focusTarget.clone().add(new THREE.Vector3(90, 60, 90))
      this.camera.position.lerp(camTarget, 0.045)
      this.controls.target.lerp(this.focusTarget, 0.08)
      if (this.camera.position.distanceTo(camTarget) < 1.2) this.focusTarget = null
    }
    this.controls.update()
    this.updateRobot(dt)
    // 探照头灯跟随相机 (照亮眼前的崎岖岩壁)
    if (this.headlight) this.headlight.position.copy(this.camera.position)
    this.updateLinks(dt)
    this.updateParticles(dt)
    this.updateWave(dt)
    // 死亡节点阴燃冒烟
    if (this.snapshot) {
      for (const [id, n] of Object.entries(this.snapshot.nodes)) {
        if (n.state === 'DEAD') {
          const last = this._smokeClock[id] ?? 0
          if (this.time - last > 0.55) {
            this._smokeClock[id] = this.time
            this.spawnSmoke(id, 1 + Math.floor(Math.random() * 2))
          }
        }
      }
    }
    // fx 生命周期
    for (let i = this.fx.length - 1; i >= 0; i--) {
      const f = this.fx[i]
      f.life += dt
      const k = Math.min(1, f.life / f.ttl)
      f.tick(f.obj, k, dt)
      if (k >= 1) {
        this.scene.remove(f.obj)
        f.obj.material?.dispose?.()
        f.obj.geometry?.dispose?.()
        this.fx.splice(i, 1)
      }
    }
    this.composer.render()
  }

  dispose() {
    this.renderer.dispose()
    this.container.innerHTML = ''
  }
}

/* 轻量轨道控制器 (拖拽旋转/滚轮缩放) */
class OrbitControlsLite {
  constructor(camera, dom) {
    this.camera = camera
    this.dom = dom
    this.target = new THREE.Vector3(0, 0, -20)
    this.spherical = new THREE.Spherical().setFromVector3(
      camera.position.clone().sub(this.target))
    this.dragging = false
    dom.addEventListener('pointerdown', (e) => {
      this.dragging = true
      this._lx = e.clientX; this._ly = e.clientY
    })
    window.addEventListener('pointerup', () => (this.dragging = false))
    window.addEventListener('pointermove', (e) => {
      if (!this.dragging) return
      this.spherical.theta -= (e.clientX - this._lx) * 0.005
      this.spherical.phi = THREE.MathUtils.clamp(
        this.spherical.phi - (e.clientY - this._ly) * 0.005, 0.15, Math.PI / 2.05)
      this._lx = e.clientX; this._ly = e.clientY
    })
    dom.addEventListener('wheel', (e) => {
      this.spherical.radius = THREE.MathUtils.clamp(
        this.spherical.radius * (1 + Math.sign(e.deltaY) * 0.08), 20, 3600)
    }, { passive: true })
  }
  update() {
    this.camera.position.setFromSpherical(this.spherical).add(this.target)
    this.camera.lookAt(this.target)
  }
}
