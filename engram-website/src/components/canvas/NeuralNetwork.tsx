"use client";

import { useRef, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

interface NeuralNetworkProps {
  scrollProgress: number;
}

// Generate a network of neurons with organic clustering
function generateNetwork(nodeCount: number, connectionDensity: number) {
  const nodes: { pos: THREE.Vector3; cluster: number; size: number }[] = [];
  const connections: { from: number; to: number; strength: number }[] = [];

  // Create 5 clusters (representing the 5 LLM providers)
  const clusterCenters = [
    new THREE.Vector3(-4, 2, -2),
    new THREE.Vector3(4, 1, -1),
    new THREE.Vector3(0, 4, -3),
    new THREE.Vector3(-3, -2, 0),
    new THREE.Vector3(3, -2.5, -2),
  ];

  // One central hub node (the User)
  nodes.push({
    pos: new THREE.Vector3(0, 0, 0),
    cluster: -1,
    size: 1.4,
  });

  // Generate clustered nodes
  for (let i = 0; i < nodeCount; i++) {
    const clusterIdx = Math.floor(Math.random() * clusterCenters.length);
    const center = clusterCenters[clusterIdx];
    const offset = new THREE.Vector3(
      (Math.random() - 0.5) * 3,
      (Math.random() - 0.5) * 2.5,
      (Math.random() - 0.5) * 3
    );
    nodes.push({
      pos: center.clone().add(offset),
      cluster: clusterIdx,
      size: 0.3 + Math.random() * 0.6,
    });
  }

  // Create intra-cluster connections
  for (let i = 1; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dist = nodes[i].pos.distanceTo(nodes[j].pos);
      if (dist < 3 && Math.random() < connectionDensity) {
        connections.push({
          from: i,
          to: j,
          strength: 1 - dist / 3,
        });
      }
    }
  }

  // Cross-cluster connections (through the hub) -- these appear on scroll
  for (let c = 0; c < clusterCenters.length; c++) {
    const clusterNodes = nodes
      .map((n, idx) => ({ ...n, idx }))
      .filter((n) => n.cluster === c);

    if (clusterNodes.length > 0) {
      // Connect closest cluster node to hub
      const closest = clusterNodes.reduce((a, b) =>
        a.pos.distanceTo(nodes[0].pos) < b.pos.distanceTo(nodes[0].pos)
          ? a
          : b
      );
      connections.push({
        from: 0,
        to: closest.idx,
        strength: 0.8,
      });
    }
  }

  return { nodes, connections };
}

export default function NeuralNetwork({ scrollProgress }: NeuralNetworkProps) {
  const groupRef = useRef<THREE.Group>(null);
  const nodesRef = useRef<THREE.InstancedMesh>(null);
  const glowRef = useRef<THREE.InstancedMesh>(null);
  const linesRef = useRef<THREE.LineSegments>(null);
  const pulseRef = useRef<THREE.Points>(null);

  const { nodes, connections, nodePositions, linePositions, lineColors, pulsePositions, pulseVelocities } =
    useMemo(() => {
      const { nodes, connections } = generateNetwork(45, 0.35);

      // Instance matrices for nodes
      const nodePositions = nodes.map((n) => n.pos);

      // Line geometry (pairs of points for each connection)
      const linePts: number[] = [];
      const lineColorsArr: number[] = [];

      const warmColor = new THREE.Color("#E09F3E");
      const coolColor = new THREE.Color("#4F46E5");

      for (const conn of connections) {
        const a = nodes[conn.from].pos;
        const b = nodes[conn.to].pos;
        linePts.push(a.x, a.y, a.z, b.x, b.y, b.z);

        // Hub connections are warm, others are cool
        const color = conn.from === 0 ? warmColor : coolColor;
        const alpha = conn.strength;
        lineColorsArr.push(
          color.r * alpha,
          color.g * alpha,
          color.b * alpha,
          color.r * alpha,
          color.g * alpha,
          color.b * alpha
        );
      }

      const linePositions = new Float32Array(linePts);
      const lineColors = new Float32Array(lineColorsArr);

      // Pulse particles that travel along connections
      const numPulses = connections.length * 2;
      const pulsePosArr = new Float32Array(numPulses * 3);
      const pulseVelArr: {
        from: THREE.Vector3;
        to: THREE.Vector3;
        t: number;
        speed: number;
      }[] = [];

      for (let i = 0; i < numPulses; i++) {
        const conn = connections[i % connections.length];
        const a = nodes[conn.from].pos;
        const b = nodes[conn.to].pos;
        const t = Math.random();
        const pos = a.clone().lerp(b, t);
        pulsePosArr[i * 3] = pos.x;
        pulsePosArr[i * 3 + 1] = pos.y;
        pulsePosArr[i * 3 + 2] = pos.z;
        pulseVelArr.push({
          from: a.clone(),
          to: b.clone(),
          t,
          speed: 0.2 + Math.random() * 0.4,
        });
      }

      return {
        nodes,
        connections,
        nodePositions,
        linePositions,
        lineColors,
        pulsePositions: pulsePosArr,
        pulseVelocities: pulseVelArr,
      };
    }, []);

  const dummy = useMemo(() => new THREE.Object3D(), []);
  const color = useMemo(() => new THREE.Color(), []);

  // Initialize instance matrices
  useFrame((state) => {
    if (!nodesRef.current || !glowRef.current) return;

    const time = state.clock.elapsedTime;

    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      // Organic breathing motion
      const breathe = Math.sin(time * 0.5 + i * 0.7) * 0.08;
      const wobbleX = Math.sin(time * 0.3 + i * 1.3) * 0.05;
      const wobbleY = Math.cos(time * 0.4 + i * 0.9) * 0.05;

      dummy.position.set(
        node.pos.x + wobbleX,
        node.pos.y + wobbleY,
        node.pos.z
      );

      const baseScale = node.size * (0.06 + breathe * 0.3);
      // Hub node pulses more prominently
      const scale =
        i === 0
          ? baseScale * (1.2 + Math.sin(time * 0.8) * 0.15)
          : baseScale;
      dummy.scale.setScalar(scale);
      dummy.updateMatrix();
      nodesRef.current.setMatrixAt(i, dummy.matrix);

      // Color: hub is warm gold, others are based on cluster
      const clusterColors = ["#10A37F", "#D97706", "#4285F4", "#E2DFD6", "#6366F1"];
      if (i === 0) {
        color.set("#E09F3E");
      } else {
        color.set(clusterColors[node.cluster] || "#4F46E5");
        // Dim nodes until scroll reveals connections
        const dim = scrollProgress < 0.3 ? 0.4 : 0.85;
        color.multiplyScalar(dim);
      }
      nodesRef.current.setColorAt(i, color);

      // Glow shell
      dummy.scale.setScalar(scale * 3);
      dummy.updateMatrix();
      glowRef.current.setMatrixAt(i, dummy.matrix);
      const glowColor = i === 0 ? new THREE.Color("#E09F3E") : color.clone();
      glowColor.multiplyScalar(0.3);
      glowRef.current.setColorAt(i, glowColor);
    }

    nodesRef.current.instanceMatrix.needsUpdate = true;
    if (nodesRef.current.instanceColor)
      nodesRef.current.instanceColor.needsUpdate = true;
    glowRef.current.instanceMatrix.needsUpdate = true;
    if (glowRef.current.instanceColor)
      glowRef.current.instanceColor.needsUpdate = true;

    // Animate pulse particles along connections
    if (pulseRef.current) {
      const posAttr = pulseRef.current.geometry.getAttribute(
        "position"
      ) as THREE.BufferAttribute;
      const arr = posAttr.array as Float32Array;

      for (let i = 0; i < pulseVelocities.length; i++) {
        const p = pulseVelocities[i];
        p.t += state.clock.getDelta() * p.speed;
        if (p.t > 1) p.t = 0;

        const pos = p.from.clone().lerp(p.to, p.t);
        arr[i * 3] = pos.x;
        arr[i * 3 + 1] = pos.y;
        arr[i * 3 + 2] = pos.z;
      }
      posAttr.needsUpdate = true;
    }

    // Line opacity based on scroll
    if (linesRef.current) {
      const mat = linesRef.current.material as THREE.LineBasicMaterial;
      mat.opacity = THREE.MathUtils.lerp(
        mat.opacity,
        scrollProgress < 0.2 ? 0.08 : scrollProgress < 0.5 ? 0.25 : 0.15,
        0.03
      );
    }

    // Gentle rotation of the whole network
    if (groupRef.current) {
      groupRef.current.rotation.y =
        time * 0.03 + Math.sin(time * 0.1) * 0.05;
      groupRef.current.rotation.x = Math.sin(time * 0.08) * 0.02;
    }
  });

  const lineGeometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(linePositions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(lineColors, 3));
    return geo;
  }, [linePositions, lineColors]);

  return (
    <group ref={groupRef}>
      {/* Neural nodes (instanced for performance) */}
      <instancedMesh
        ref={nodesRef}
        args={[undefined, undefined, nodes.length]}
      >
        <sphereGeometry args={[1, 16, 16]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>

      {/* Glow shells */}
      <instancedMesh
        ref={glowRef}
        args={[undefined, undefined, nodes.length]}
      >
        <sphereGeometry args={[1, 8, 8]} />
        <meshBasicMaterial
          transparent
          opacity={0.06}
          depthWrite={false}
          side={THREE.BackSide}
          blending={THREE.AdditiveBlending}
        />
      </instancedMesh>

      {/* Connection lines */}
      <lineSegments ref={linesRef} geometry={lineGeometry}>
        <lineBasicMaterial
          vertexColors
          transparent
          opacity={0.12}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </lineSegments>

      {/* Pulse particles traveling along connections */}
      <points ref={pulseRef}>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            args={[pulsePositions, 3]}
          />
        </bufferGeometry>
        <pointsMaterial
          color="#E09F3E"
          size={0.04}
          transparent
          opacity={0.8}
          sizeAttenuation
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </points>
    </group>
  );
}
