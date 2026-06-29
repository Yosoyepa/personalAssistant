import React from "react";
import { Composition, registerRoot } from "remotion";
import { PersonalAssistantArchitecture } from "./PersonalAssistantArchitecture";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="PersonalAssistantArchitecture"
      component={PersonalAssistantArchitecture}
      durationInFrames={720}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};

registerRoot(RemotionRoot);
