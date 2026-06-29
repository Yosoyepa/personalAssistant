import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { architectureSlides } from "./slides";
import type { ArchitectureLink, ArchitectureNode } from "./slides";

const SLIDE_FRAMES = 180;

const palette: Record<ArchitectureNode["tone"], string> = {
  artifact: "#f97316",
  core: "#2563eb",
  guardrail: "#dc2626",
  memory: "#059669",
  signal: "#7c3aed",
  tool: "#0d9488",
};

const backgroundStyle: React.CSSProperties = {
  background:
    "radial-gradient(circle at 16% 16%, rgba(37, 99, 235, 0.18), transparent 30%), radial-gradient(circle at 82% 18%, rgba(249, 115, 22, 0.15), transparent 28%), linear-gradient(135deg, #f8fafc 0%, #eef2f7 48%, #fff7ed 100%)",
  color: "#111827",
  fontFamily:
    'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
};

const nodeStyle: React.CSSProperties = {
  position: "absolute",
  width: 260,
  minHeight: 132,
  borderRadius: 8,
  backgroundColor: "rgba(255, 255, 255, 0.86)",
  border: "2px solid rgba(17, 24, 39, 0.12)",
  boxShadow: "0 18px 42px rgba(17, 24, 39, 0.12)",
  padding: "22px 24px",
};

const pathForLink = (
  from: ArchitectureNode,
  to: ArchitectureNode,
): string => {
  const startX = from.x + 260;
  const startY = from.y + 66;
  const endX = to.x;
  const endY = to.y + 66;
  const bend = Math.max(120, Math.abs(endX - startX) * 0.42);

  return `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`;
};

const AnimatedLink: React.FC<{
  link: ArchitectureLink;
  nodes: ArchitectureNode[];
  progress: number;
  index: number;
}> = ({ link, nodes, progress, index }) => {
  const from = nodes.find((node) => node.id === link.from);
  const to = nodes.find((node) => node.id === link.to);

  if (!from || !to) {
    return null;
  }

  const color = palette[to.tone];
  const reveal = interpolate(progress, [0.12 + index * 0.08, 0.42 + index * 0.08], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const labelX = (from.x + to.x) / 2 + 130;
  const labelY = (from.y + to.y) / 2 + 32;

  return (
    <g opacity={0.9}>
      <path
        d={pathForLink(from, to)}
        fill="none"
        stroke={color}
        strokeDasharray="12 14"
        strokeDashoffset={(1 - reveal) * 260}
        strokeLinecap="round"
        strokeWidth={5}
      />
      <circle
        cx={to.x}
        cy={to.y + 66}
        fill={color}
        opacity={reveal}
        r={9}
      />
      <text
        fill="#374151"
        fontFamily="ui-sans-serif, system-ui, sans-serif"
        fontSize={24}
        fontWeight={700}
        opacity={reveal}
        x={labelX}
        y={labelY}
      >
        {link.label}
      </text>
    </g>
  );
};

const ArchitectureNodeCard: React.FC<{
  node: ArchitectureNode;
  index: number;
  progress: number;
}> = ({ node, index, progress }) => {
  const delay = index * 0.07;
  const appear = interpolate(progress, [delay, delay + 0.22], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const lift = interpolate(appear, [0, 1], [34, 0]);
  const color = palette[node.tone];

  return (
    <div
      style={{
        ...nodeStyle,
        borderColor: `${color}55`,
        left: node.x,
        opacity: appear,
        top: node.y + lift,
      }}
    >
      <div
        style={{
          alignItems: "center",
          display: "flex",
          gap: 14,
          marginBottom: 14,
        }}
      >
        <div
          style={{
            backgroundColor: color,
            borderRadius: 8,
            height: 34,
            width: 34,
          }}
        />
        <div
          style={{
            color,
            fontSize: 24,
            fontWeight: 800,
            lineHeight: 1,
          }}
        >
          {node.id.toUpperCase()}
        </div>
      </div>
      <div
        style={{
          color: "#111827",
          fontSize: 35,
          fontWeight: 850,
          lineHeight: 1.08,
          marginBottom: 12,
        }}
      >
        {node.label}
      </div>
      <div
        style={{
          color: "#4b5563",
          fontSize: 24,
          fontWeight: 600,
          lineHeight: 1.22,
        }}
      >
        {node.detail}
      </div>
    </div>
  );
};

const CheckpointList: React.FC<{
  items: string[];
  progress: number;
}> = ({ items, progress }) => {
  return (
    <div
      style={{
        bottom: 142,
        display: "grid",
        gap: 12,
        left: 108,
        position: "absolute",
        width: 365,
      }}
    >
      {items.map((item, index) => {
        const reveal = interpolate(
          progress,
          [0.45 + index * 0.08, 0.62 + index * 0.08],
          [0, 1],
          {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          },
        );

        return (
          <div
            key={item}
            style={{
              alignItems: "center",
              backgroundColor: "rgba(255, 255, 255, 0.72)",
              border: "1px solid rgba(17, 24, 39, 0.10)",
              borderRadius: 8,
              boxShadow: "0 12px 28px rgba(17, 24, 39, 0.08)",
              display: "flex",
              gap: 16,
              minHeight: 54,
              opacity: reveal,
              padding: "10px 16px",
              transform: `translateX(${interpolate(reveal, [0, 1], [-22, 0])}px)`,
            }}
          >
            <div
              style={{
                backgroundColor: "#059669",
                borderRadius: 6,
                height: 22,
                width: 22,
              }}
            />
            <div
              style={{
                color: "#1f2937",
                fontSize: 21,
                fontWeight: 720,
                lineHeight: 1.16,
              }}
            >
              {item}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const Timeline: React.FC<{
  activeIndex: number;
  progress: number;
}> = ({ activeIndex, progress }) => {
  return (
    <div
      style={{
        bottom: 84,
        display: "flex",
        gap: 12,
        position: "absolute",
        right: 108,
      }}
    >
      {architectureSlides.map((slide, index) => {
        const active = index === activeIndex;

        return (
          <div
            key={slide.focus}
            style={{
              backgroundColor: active ? "#111827" : "rgba(17, 24, 39, 0.16)",
              borderRadius: 8,
              height: 12,
              overflow: "hidden",
              width: 118,
            }}
          >
            {active ? (
              <div
                style={{
                  backgroundColor: "#f97316",
                  height: "100%",
                  width: `${Math.round(progress * 100)}%`,
                }}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
};

const slideIndexForFrame = (frame: number): number => {
  return Math.min(
    architectureSlides.length - 1,
    Math.floor(frame / SLIDE_FRAMES),
  );
};

export const PersonalAssistantArchitecture: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const activeIndex = slideIndexForFrame(frame);
  const slide = architectureSlides[activeIndex];
  const localFrame = frame - activeIndex * SLIDE_FRAMES;
  const progress = localFrame / SLIDE_FRAMES;
  const titleSpring = spring({
    fps,
    frame: localFrame,
    config: {
      damping: 18,
      mass: 0.55,
      stiffness: 110,
    },
  });
  const titleY = interpolate(titleSpring, [0, 1], [24, 0]);
  const exitFade = interpolate(progress, [0.86, 1], [1, 0.2], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={backgroundStyle}>
      <div
        style={{
          height: "100%",
          opacity: exitFade,
          position: "relative",
          width: "100%",
        }}
      >
        <div
          style={{
            left: 108,
            position: "absolute",
            top: 72 + titleY,
            width: 900,
          }}
        >
          <div
            style={{
              color: palette[slide.nodes[0].tone],
              fontSize: 30,
              fontWeight: 850,
              lineHeight: 1,
              marginBottom: 18,
              textTransform: "uppercase",
            }}
          >
            {slide.focus}
          </div>
          <div
            style={{
              color: "#111827",
              fontSize: 74,
              fontWeight: 900,
              lineHeight: 0.98,
              marginBottom: 22,
              maxWidth: 920,
            }}
          >
            {slide.title}
          </div>
          <div
            style={{
              color: "#374151",
              fontSize: 32,
              fontWeight: 620,
              lineHeight: 1.22,
              maxWidth: 760,
            }}
          >
            {slide.subtitle}
          </div>
        </div>

        <div
          style={{
            backgroundColor: "rgba(255, 255, 255, 0.56)",
            border: "1px solid rgba(17, 24, 39, 0.10)",
            borderRadius: 8,
            height: 600,
            left: 520,
            position: "absolute",
            top: 330,
            width: 1300,
          }}
        />

        <svg
          height="600"
          style={{
            left: 520,
            overflow: "visible",
            position: "absolute",
            top: 330,
          }}
          viewBox="0 0 1300 600"
          width="1300"
        >
          {slide.links.map((link, index) => (
            <AnimatedLink
              index={index}
              key={`${link.from}-${link.to}`}
              link={link}
              nodes={slide.nodes}
              progress={progress}
            />
          ))}
        </svg>

        <div
          style={{
            left: 520,
            position: "absolute",
            top: 330,
          }}
        >
          {slide.nodes.map((node, index) => (
            <ArchitectureNodeCard
              index={index}
              key={node.id}
              node={node}
              progress={progress}
            />
          ))}
        </div>

        <CheckpointList items={slide.checkpoints} progress={progress} />
        <Timeline activeIndex={activeIndex} progress={progress} />

        <div
          style={{
            borderTop: "2px solid rgba(17, 24, 39, 0.12)",
            color: "#4b5563",
            fontSize: 24,
            fontWeight: 700,
            left: 108,
            paddingTop: 20,
            position: "absolute",
            right: 108,
            top: 980,
          }}
        >
          Personal Assistant Architecture / Remotion public artifact
        </div>
      </div>
    </AbsoluteFill>
  );
};
