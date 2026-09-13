import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

const STAGES = [
  "Stage 01 · Calibration Datum",
  "Stage 02 · Axles & Drivetrain",
  "Stage 03 · Chassis Monocoque",
  "Stage 04 · Hexagon NPU Payload",
  "Stage 05 · Multi-Modal Sensor Rig",
  "Stage 06 · Active LiDAR Sweep",
];

export default function RobotViewer3D({ poseTrace = [], deploymentId = null }) {
  const containerRef = useRef(null);
  const animFrameRef = useRef(null);
  const robotGroupRef = useRef(null);
  const lidarRef = useRef(null);
  const frustumGroupRef = useRef(null);
  const beaconLightRef = useRef(null);
  const subassembliesRef = useRef([]);

  const [loadingStage, setLoadingStage] = useState(0);
  const [loadingComplete, setLoadingComplete] = useState(false);
  const [viewMode, setViewMode] = useState("solid"); // solid | wireframe | xray
  const [showSensors, setShowSensors] = useState(false);
  const [autoRotate, setAutoRotate] = useState(false);
  const [telemetry, setTelemetry] = useState({ x: 0.0, y: 0.0, heading: 0, state: "OPERATIONAL" });

  const controlsRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth || 400;
    const height = container.clientHeight || 480;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#eef0ea");

    // Camera — positioned for optimal 3/4 robotic framing
    const camera = new THREE.PerspectiveCamera(36, width / height, 0.1, 100);
    camera.position.set(2.8, 2.0, 3.0);

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    container.appendChild(renderer.domElement);

    // Orbit Controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxPolarAngle = Math.PI / 2 - 0.02;
    controls.minDistance = 1.2;
    controls.maxDistance = 6.0;
    controls.target.set(0, 0.4, 0);
    controlsRef.current = controls;

    // Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 1.3);
    scene.add(ambientLight);

    const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
    keyLight.position.set(4, 7, 5);
    keyLight.castShadow = true;
    keyLight.shadow.mapSize.width = 1024;
    keyLight.shadow.mapSize.height = 1024;
    keyLight.shadow.bias = -0.0008;
    scene.add(keyLight);

    const fillLight = new THREE.DirectionalLight(0x8da0cb, 0.7);
    fillLight.position.set(-4, 3, -3);
    scene.add(fillLight);

    // Ground Calibration Radar & Grid
    const groundGroup = new THREE.Group();

    // 1. Grid
    const grid = new THREE.GridHelper(6, 24, 0x12151a, 0xc2c8be);
    grid.position.y = 0.001;
    grid.material.opacity = 0.55;
    grid.material.transparent = true;
    groundGroup.add(grid);

    // 2. Concentric Range Rings (0.6m, 1.2m, 1.8m, 2.4m)
    const ringMat = new THREE.LineBasicMaterial({
      color: 0x14b09a,
      transparent: true,
      opacity: 0.35,
    });
    [0.6, 1.2, 1.8, 2.4].forEach((radius) => {
      const ringGeo = new THREE.BufferGeometry();
      const points = [];
      const segs = 64;
      for (let i = 0; i <= segs; i++) {
        const theta = (i / segs) * Math.PI * 2;
        points.push(new THREE.Vector3(Math.cos(theta) * radius, 0.002, Math.sin(theta) * radius));
      }
      ringGeo.setFromPoints(points);
      const ringLine = new THREE.Line(ringGeo, ringMat);
      groundGroup.add(ringLine);
    });

    // 3. Shadow Receiver Plane
    const planeGeo = new THREE.PlaneGeometry(8, 8);
    const planeMat = new THREE.ShadowMaterial({ opacity: 0.18 });
    const shadowPlane = new THREE.Mesh(planeGeo, planeMat);
    shadowPlane.rotation.x = -Math.PI / 2;
    shadowPlane.receiveShadow = true;
    groundGroup.add(shadowPlane);

    scene.add(groundGroup);

    // High-Contrast Robotics Material Library
    const mats = {
      chassis: new THREE.MeshStandardMaterial({
        color: 0x15191e,
        roughness: 0.35,
        metalness: 0.8,
      }),
      monocoque: new THREE.MeshStandardMaterial({
        color: 0x252c36,
        roughness: 0.28,
        metalness: 0.65,
      }),
      accentOrange: new THREE.MeshBasicMaterial({
        color: 0xff5b2e,
      }),
      wheelRubber: new THREE.MeshStandardMaterial({
        color: 0x0e1013,
        roughness: 0.9,
        metalness: 0.08,
      }),
      alloyRim: new THREE.MeshStandardMaterial({
        color: 0xd2d8e0,
        roughness: 0.2,
        metalness: 0.9,
      }),
      npuProcessor: new THREE.MeshStandardMaterial({
        color: 0x06584e,
        roughness: 0.15,
        metalness: 0.95,
        emissive: 0x14b09a,
        emissiveIntensity: 0.65,
      }),
      beacon: new THREE.MeshBasicMaterial({
        color: 0xff5b2e,
      }),
      lidarLens: new THREE.MeshStandardMaterial({
        color: 0x22307e,
        roughness: 0.1,
        metalness: 0.9,
      }),
      sensorCone: new THREE.MeshBasicMaterial({
        color: 0x14b09a,
        transparent: true,
        opacity: 0.1,
        side: THREE.DoubleSide,
      }),
    };

    // Construct AMR Sub-assemblies
    const robot = new THREE.Group();
    scene.add(robot);
    robotGroupRef.current = robot;

    const subassemblies = [];

    // --- Subassembly 0: Axles & Suspension ---
    const axleGroup = new THREE.Group();
    const axleGeo = new THREE.CylinderGeometry(0.022, 0.022, 0.78, 14);
    axleGeo.rotateZ(Math.PI / 2);
    const frontAxle = new THREE.Mesh(axleGeo, mats.alloyRim);
    frontAxle.position.set(0, 0.14, 0.34);
    frontAxle.castShadow = true;
    const rearAxle = new THREE.Mesh(axleGeo, mats.alloyRim);
    rearAxle.position.set(0, 0.14, -0.34);
    rearAxle.castShadow = true;
    axleGroup.add(frontAxle, rearAxle);
    subassemblies.push(axleGroup);
    robot.add(axleGroup);

    // --- Subassembly 1: 4 Drive Wheels & Alloy Hubs ---
    const wheelsGroup = new THREE.Group();
    const wheelGeo = new THREE.CylinderGeometry(0.14, 0.14, 0.09, 24);
    wheelGeo.rotateZ(Math.PI / 2);
    const rimGeo = new THREE.CylinderGeometry(0.095, 0.095, 0.096, 16);
    rimGeo.rotateZ(Math.PI / 2);
    const hubCapGeo = new THREE.CylinderGeometry(0.035, 0.035, 0.098, 12);
    hubCapGeo.rotateZ(Math.PI / 2);

    const wheelPositions = [
      [-0.41, 0.14, 0.34],
      [0.41, 0.14, 0.34],
      [-0.41, 0.14, -0.34],
      [0.41, 0.14, -0.34],
    ];

    wheelPositions.forEach(([wx, wy, wz]) => {
      const tire = new THREE.Mesh(wheelGeo, mats.wheelRubber);
      tire.castShadow = true;
      const rim = new THREE.Mesh(rimGeo, mats.alloyRim);
      const cap = new THREE.Mesh(hubCapGeo, mats.accentOrange);
      tire.position.set(wx, wy, wz);
      rim.position.set(wx, wy, wz);
      cap.position.set(wx, wy, wz);
      wheelsGroup.add(tire, rim, cap);
    });
    subassemblies.push(wheelsGroup);
    robot.add(wheelsGroup);

    // --- Subassembly 2: Lower Chassis Baseplate & Bumpers ---
    const chassisGroup = new THREE.Group();
    const baseplate = new THREE.Mesh(
      new RoundedBoxGeometry(0.74, 0.08, 1.06, 3, 0.02),
      mats.chassis
    );
    baseplate.position.set(0, 0.22, 0);
    baseplate.castShadow = true;
    baseplate.receiveShadow = true;

    // Safety orange perimeter stripe
    const stripe = new THREE.Mesh(
      new RoundedBoxGeometry(0.75, 0.015, 1.07, 2, 0.005),
      mats.accentOrange
    );
    stripe.position.set(0, 0.25, 0);

    // Front/Rear Impact Bumpers
    const bumperF = new THREE.Mesh(
      new RoundedBoxGeometry(0.78, 0.07, 0.08, 2, 0.01),
      mats.chassis
    );
    bumperF.position.set(0, 0.21, 0.54);
    bumperF.castShadow = true;

    const bumperR = new THREE.Mesh(
      new RoundedBoxGeometry(0.78, 0.07, 0.08, 2, 0.01),
      mats.chassis
    );
    bumperR.position.set(0, 0.21, -0.54);
    bumperR.castShadow = true;

    chassisGroup.add(baseplate, stripe, bumperF, bumperR);
    subassemblies.push(chassisGroup);
    robot.add(chassisGroup);

    // --- Subassembly 3: Monocoque Shell & Qualcomm Hexagon NPU Payload ---
    const bodyGroup = new THREE.Group();
    const upperCasing = new THREE.Mesh(
      new RoundedBoxGeometry(0.66, 0.25, 0.9, 4, 0.035),
      mats.monocoque
    );
    upperCasing.position.set(0, 0.38, 0);
    upperCasing.castShadow = true;
    upperCasing.receiveShadow = true;

    // Hexagon NPU Processor Die
    const npuBadge = new THREE.Mesh(
      new RoundedBoxGeometry(0.26, 0.02, 0.26, 2, 0.008),
      mats.npuProcessor
    );
    npuBadge.position.set(0, 0.51, 0.04);

    // Heat sink fins
    for (let f = -0.08; f <= 0.08; f += 0.04) {
      const fin = new THREE.Mesh(
        new THREE.BoxGeometry(0.24, 0.01, 0.012),
        mats.alloyRim
      );
      fin.position.set(0, 0.525, 0.04 + f);
      bodyGroup.add(fin);
    }

    bodyGroup.add(upperCasing, npuBadge);
    subassemblies.push(bodyGroup);
    robot.add(bodyGroup);

    // --- Subassembly 4: Sensor Mast & Dual Perception Cameras ---
    const sensorMastGroup = new THREE.Group();
    const mast = new THREE.Mesh(
      new THREE.CylinderGeometry(0.032, 0.032, 0.34, 16),
      mats.chassis
    );
    mast.position.set(0, 0.67, 0.24);
    mast.castShadow = true;

    // Stereoscopic Camera Bar
    const camBar = new THREE.Mesh(
      new RoundedBoxGeometry(0.32, 0.05, 0.055, 2, 0.008),
      mats.chassis
    );
    camBar.position.set(0, 0.72, 0.26);

    // Left/Right Optical Lenses
    const lensL = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.022, 16), mats.lidarLens);
    lensL.rotateX(Math.PI / 2);
    lensL.position.set(-0.11, 0.72, 0.29);
    const lensR = lensL.clone();
    lensR.position.x = 0.11;

    // Signal Beacon atop mast
    const beaconSphere = new THREE.Mesh(new THREE.SphereGeometry(0.045, 16, 12), mats.beacon);
    beaconSphere.position.set(0, 0.85, -0.26);

    const beaconLight = new THREE.PointLight(0xff5b2e, 0.8, 2.5);
    beaconLight.position.set(0, 0.85, -0.26);
    sensorMastGroup.add(beaconLight);
    beaconLightRef.current = beaconLight;

    sensorMastGroup.add(mast, camBar, lensL, lensR, beaconSphere);
    subassemblies.push(sensorMastGroup);
    robot.add(sensorMastGroup);

    // --- Subassembly 5: Spinning LiDAR Puck ---
    const lidarGroup = new THREE.Group();
    const lidarBase = new THREE.Mesh(
      new THREE.CylinderGeometry(0.075, 0.075, 0.08, 24),
      mats.chassis
    );
    lidarBase.position.set(0, 0.84, 0.24);

    const lidarRotor = new THREE.Mesh(
      new THREE.CylinderGeometry(0.07, 0.07, 0.055, 24),
      mats.lidarLens
    );
    lidarRotor.position.set(0, 0.88, 0.24);
    lidarRef.current = lidarRotor;

    lidarGroup.add(lidarBase, lidarRotor);
    subassemblies.push(lidarGroup);
    robot.add(lidarGroup);

    // --- Visual Sensor Field Container (Toggled via Sensors button) ---
    const frustaGroup = new THREE.Group();

    const sensorLineMat = new THREE.LineBasicMaterial({
      color: 0x14b09a,
      transparent: true,
      opacity: 0.7,
    });

    // 1. Radial LiDAR Laser Scan Rays (16 rays in a 120° forward arc)
    const lidarRaysGeo = new THREE.BufferGeometry();
    const rayPoints = [];
    const numRays = 16;
    const arcAngle = Math.PI * 0.65;
    const startAngle = (Math.PI - arcAngle) / 2;
    const rayLength = 1.35;
    const origin = new THREE.Vector3(0, 0.88, 0.24);

    for (let i = 0; i <= numRays; i++) {
      const angle = startAngle + (i / numRays) * arcAngle;
      const targetX = Math.cos(angle) * rayLength;
      const targetZ = 0.24 + Math.sin(angle) * rayLength;
      rayPoints.push(origin.x, origin.y, origin.z);
      rayPoints.push(targetX, 0.88, targetZ);
    }
    lidarRaysGeo.setAttribute("position", new THREE.Float32BufferAttribute(rayPoints, 3));
    const lidarRays = new THREE.LineSegments(lidarRaysGeo, sensorLineMat);
    frustaGroup.add(lidarRays);

    // 2. Arc Perimeter Connecting Ray Ends
    const arcPerimGeo = new THREE.BufferGeometry();
    const perimPoints = [];
    for (let i = 0; i <= 32; i++) {
      const angle = startAngle + (i / 32) * arcAngle;
      perimPoints.push(new THREE.Vector3(Math.cos(angle) * rayLength, 0.88, 0.24 + Math.sin(angle) * rayLength));
    }
    arcPerimGeo.setFromPoints(perimPoints);
    const arcPerim = new THREE.Line(arcPerimGeo, sensorLineMat);
    frustaGroup.add(arcPerim);

    // 3. Stereoscopic Camera Forward FOV Guidance Lines (Safety Orange)
    const camRaysGeo = new THREE.BufferGeometry();
    const camPoints = [
      new THREE.Vector3(-0.11, 0.72, 0.29),
      new THREE.Vector3(-0.38, 0.68, 1.3),
      new THREE.Vector3(0.11, 0.72, 0.29),
      new THREE.Vector3(0.38, 0.68, 1.3),
      new THREE.Vector3(-0.38, 0.68, 1.3),
      new THREE.Vector3(0.38, 0.68, 1.3),
    ];
    camRaysGeo.setFromPoints(camPoints);
    const camRaysMat = new THREE.LineBasicMaterial({
      color: 0xff5b2e,
      transparent: true,
      opacity: 0.75,
    });
    const camRays = new THREE.LineSegments(camRaysGeo, camRaysMat);
    frustaGroup.add(camRays);

    frustaGroup.visible = false;
    robot.add(frustaGroup);
    frustumGroupRef.current = frustaGroup;

    subassembliesRef.current = subassemblies;

    // Holographic Assembly Sequence
    let stepInterval = null;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduced) {
      subassemblies.forEach((sub) => {
        sub.visible = true;
        sub.scale.set(1, 1, 1);
      });
      setLoadingStage(5);
      setLoadingComplete(true);
      setTelemetry((prev) => ({ ...prev, state: "OPERATIONAL" }));
    } else {
      subassemblies.forEach((sub) => {
        sub.visible = false;
        sub.scale.set(0.001, 0.001, 0.001);
      });

      let currentStep = 0;
      stepInterval = setInterval(() => {
        if (currentStep < subassemblies.length) {
          const sub = subassemblies[currentStep];
          if (sub) {
            sub.visible = true;
            sub.scale.set(1.06, 1.06, 1.06);
            setTimeout(() => {
              if (sub) sub.scale.set(1, 1, 1);
            }, 100);
          }
          setLoadingStage(currentStep);
          currentStep++;
        } else {
          clearInterval(stepInterval);
          stepInterval = null;
          setLoadingComplete(true);
          setTelemetry((prev) => ({ ...prev, state: "OPERATIONAL" }));
        }
      }, 200);
    }

    // Main Animation Loop
    let time = 0;

    const animate = () => {
      time += 0.016;

      // Spin LiDAR puck
      if (lidarRef.current) {
        lidarRef.current.rotation.y += 0.09;
      }

      // Beacon breathing glow
      const pulse = 0.5 + Math.sin(time * 5) * 0.5;
      if (beaconLightRef.current) {
        beaconLightRef.current.intensity = 0.4 + pulse * 0.8;
      }

      // Waypoint Trajectory Navigation Playback
      if (poseTrace && poseTrace.length > 1) {
        const step = (time * 0.85) % (poseTrace.length - 1);
        const idx = Math.floor(step);
        const frac = step - idx;
        const p1 = poseTrace[idx];
        const p2 = poseTrace[idx + 1] || p1;

        if (p1 && p2) {
          const posX = (p1[0] + (p2[0] - p1[0]) * frac) * 0.75;
          const posZ = (p1[1] + (p2[1] - p1[1]) * frac) * 0.75;
          robot.position.x = posX;
          robot.position.z = posZ;

          const dx = p2[0] - p1[0];
          const dz = p2[1] - p1[1];
          if (Math.hypot(dx, dz) > 0.005) {
            const angle = Math.atan2(dx, dz);
            robot.rotation.y = angle;
            setTelemetry((prev) => ({
              ...prev,
              x: Number(posX.toFixed(2)),
              y: Number(posZ.toFixed(2)),
              heading: Math.round((angle * 180) / Math.PI),
              state: "NAVIGATING",
            }));
          }
        }
      }

      if (autoRotate && controls) {
        controls.autoRotate = true;
        controls.autoRotateSpeed = 1.4;
      } else if (controls) {
        controls.autoRotate = false;
      }

      controls.update();
      renderer.render(scene, camera);
      animFrameRef.current = requestAnimationFrame(animate);
    };

    animFrameRef.current = requestAnimationFrame(animate);

    // Resize Handler
    const handleResize = () => {
      if (!container) return;
      const nw = container.clientWidth;
      const nh = container.clientHeight;
      camera.aspect = nw / nh;
      camera.updateProjectionMatrix();
      renderer.setSize(nw, nh);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      if (stepInterval) clearInterval(stepInterval);
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      controls.dispose();
      renderer.dispose();
      if (container && container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [poseTrace, autoRotate]);

  // Handle View Mode changes
  useEffect(() => {
    if (!robotGroupRef.current) return;
    robotGroupRef.current.traverse((child) => {
      if (child.isMesh && child.material && !child.material.isShadowMaterial) {
        if (viewMode === "wireframe") {
          child.material.wireframe = true;
        } else if (viewMode === "xray") {
          child.material.wireframe = false;
          child.material.transparent = true;
          child.material.opacity = 0.35;
        } else {
          child.material.wireframe = false;
          child.material.transparent = false;
          child.material.opacity = 1.0;
        }
      }
    });
  }, [viewMode]);

  // Handle sensor cone visibility
  useEffect(() => {
    if (frustumGroupRef.current) {
      frustumGroupRef.current.visible = showSensors;
    }
  }, [showSensors]);

  // Re-run kinematic calibration sequence
  const recalibrate = () => {
    setLoadingComplete(false);
    setLoadingStage(0);
    subassembliesRef.current.forEach((sub) => {
      sub.visible = false;
      sub.scale.set(0.001, 0.001, 0.001);
    });

    let currentStep = 0;
    const interval = setInterval(() => {
      if (currentStep < subassembliesRef.current.length) {
        const sub = subassembliesRef.current[currentStep];
        if (sub) {
          sub.visible = true;
          sub.scale.set(1.06, 1.06, 1.06);
          setTimeout(() => {
            if (sub) sub.scale.set(1, 1, 1);
          }, 80);
        }
        setLoadingStage(currentStep);
        currentStep++;
      } else {
        clearInterval(interval);
        setLoadingComplete(true);
      }
    }, 180);
  };

  return (
    <div className="robot-viewport">
      <div className="robot-canvas-container" ref={containerRef} />

      {/* Holographic Assembly Overlay */}
      {!loadingComplete && (
        <div className="viewport-loading">
          <div className="assembly-radar" />
          <div className="assembly-stage-text">{STAGES[loadingStage]}</div>
          <div className="assembly-stage-progress">
            <div
              className="assembly-stage-progress-bar"
              style={{ width: `${((loadingStage + 1) / STAGES.length) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* Real-time HUD */}
      <div className="viewport-hud">
        <div className="viewport-hud__top">
          <div className="viewport-chip">
            <strong>QRB-AMR-X1</strong> · QUALCOMM HEXAGON NPU
          </div>

          <div className="viewport-hud__top-right">
            <div className="viewport-chip viewport-chip--live">
              <span className="live-dot" />
              <span>{telemetry.state}</span>
            </div>

            {/* Viewport Control Toolbar */}
            <div className="viewport-toolbar">
              <button
                type="button"
                className={`viewport-btn ${viewMode === "solid" ? "is-active" : ""}`}
                onClick={() => setViewMode("solid")}
              >
                Solid
              </button>
              <button
                type="button"
                className={`viewport-btn ${viewMode === "wireframe" ? "is-active" : ""}`}
                onClick={() => setViewMode("wireframe")}
              >
                Wire
              </button>
              <button
                type="button"
                className={`viewport-btn ${viewMode === "xray" ? "is-active" : ""}`}
                onClick={() => setViewMode("xray")}
              >
                X-Ray
              </button>
              <button
                type="button"
                className={`viewport-btn ${showSensors ? "is-active" : ""}`}
                onClick={() => setShowSensors(!showSensors)}
              >
                Sensors
              </button>
              <button
                type="button"
                className={`viewport-btn ${autoRotate ? "is-active" : ""}`}
                onClick={() => setAutoRotate(!autoRotate)}
              >
                Rotate
              </button>
              <button
                type="button"
                className="viewport-btn"
                onClick={recalibrate}
                title="Re-run kinematic calibration sequence"
              >
                Calibrate ↺
              </button>
            </div>
          </div>
        </div>

        <div className="viewport-hud__bottom">
          <div className="viewport-chip">
            X: <strong>{telemetry.x}m</strong> Y: <strong>{telemetry.y}m</strong> · HDG:{" "}
            <strong>{telemetry.heading}°</strong>
          </div>
          <div className="viewport-chip">
            BAT: <strong>98%</strong> · 60Hz ODOM · 14.8ms
          </div>
        </div>
      </div>
    </div>
  );
}
