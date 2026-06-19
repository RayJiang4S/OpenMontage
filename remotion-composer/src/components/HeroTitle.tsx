import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

interface HeroTitleProps {
  title: string;
  subtitle?: string;
}

export const HeroTitle: React.FC<HeroTitleProps> = ({ title, subtitle }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Keep per-character animation while only allowing wrapping between words.
  const titleChars = title.split("");
  let charOffset = 0;
  const titleTokens = title.split(/(\s+)/).filter((token) => token.length > 0).map((token) => {
    const start = charOffset;
    charOffset += token.length;
    return {
      token,
      start,
      isWhitespace: /^\s+$/.test(token),
    };
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "center",
        background:
          "radial-gradient(ellipse at center, rgba(15,23,42,0.35) 0%, rgba(15,23,42,0.55) 100%)",
      }}
    >
      <div style={{ textAlign: "center", maxWidth: "85%" }}>
        {/* Main title with per-character spring */}
        <div
          style={{
            fontSize: 72,
            fontWeight: 800,
            fontFamily: "Space Grotesk, Inter, system-ui, sans-serif",
            lineHeight: 1.2,
            display: "flex",
            justifyContent: "center",
            flexWrap: "wrap",
            gap: 0,
          }}
        >
          {titleTokens.map(({ token, start, isWhitespace }, tokenIndex) => {
            if (isWhitespace) {
              return (
                <span
                  key={`space-${tokenIndex}`}
                  style={{
                    display: "inline-block",
                    whiteSpace: "pre",
                    minWidth: `${Math.max(token.length, 1) * 0.3}em`,
                  }}
                >
                  {token}
                </span>
              );
            }

            return (
              <span
                key={`word-${tokenIndex}`}
                style={{
                  display: "inline-flex",
                  whiteSpace: "nowrap",
                }}
              >
                {token.split("").map((char, charIndex) => {
                  const i = start + charIndex;
                  const delay = i * 1.2;
                  const charSpring = spring({
                    frame: frame - delay,
                    fps,
                    config: { damping: 12, stiffness: 150 },
                  });

                  return (
                    <span
                      key={`${i}-${char}`}
                      style={{
                        display: "inline-block",
                        opacity: charSpring,
                        transform: `translateY(${interpolate(charSpring, [0, 1], [30, 0])}px)`,
                        color: i < 8 ? "#22D3EE" : "#F8FAFC", // Accent first word
                      }}
                    >
                      {char}
                    </span>
                  );
                })}
              </span>
            );
          })}
        </div>

        {/* Subtitle */}
        {subtitle && (
          <div
            style={{
              marginTop: 20,
              opacity: spring({
                frame: frame - titleChars.length * 1.2 - 5,
                fps,
                config: { damping: 20 },
              }),
              fontSize: 28,
              fontWeight: 400,
              color: "#A78BFA",
              fontFamily: "Space Grotesk, Inter, system-ui, sans-serif",
              letterSpacing: "0.1em",
              textTransform: "uppercase",
            }}
          >
            {subtitle}
          </div>
        )}

        {/* Animated underline */}
        <div
          style={{
            margin: "24px auto 0",
            height: 3,
            backgroundColor: "#22D3EE",
            borderRadius: 2,
            width: interpolate(
              spring({
                frame: frame - 15,
                fps,
                config: { damping: 15, stiffness: 60 },
              }),
              [0, 1],
              [0, 400]
            ),
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
