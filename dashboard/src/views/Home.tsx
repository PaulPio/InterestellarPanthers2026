import { CrewStrip } from "../components/CrewStrip";
import { EarthQueuePanel } from "../components/EarthQueuePanel";
import { MissionCard } from "../components/MissionCard";
import { RiskCards } from "../components/RiskCards";
import { StatusBar } from "../components/StatusBar";
import { Timeline } from "../components/Timeline";

export default function Home() {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="sr-only">Home</h1>
      <StatusBar />
      <div className="grid grid-cols-1 items-start gap-2 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <MissionCard />
        <CrewStrip />
      </div>
      <RiskCards />
      <div className="grid grid-cols-1 items-start gap-2 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Timeline />
        <EarthQueuePanel />
      </div>
    </div>
  );
}
