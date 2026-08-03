import { useCallback, useState } from "react";
import Layout from "@/components/Layout";
import type { AppTab } from "@/navigation";
import EskdAgentPage from "@/pages/EskdAgentPage";
import HistoryPage from "@/pages/HistoryPage";
import KnowledgeBasePage from "@/pages/KnowledgeBasePage";
import IntegrationLogPage from "@/pages/IntegrationLogPage";
import MarkingPage from "@/pages/MarkingPage";
import StatsPage from "@/pages/StatsPage";
import styles from "./App.module.css";
import type { MarkingOpenIntent } from "@/types/markingOpen";

export type { MarkingOpenIntent } from "@/types/markingOpen";

export default function App() {
  const [tab, setTab] = useState<AppTab>("check");
  const [markingIntent, setMarkingIntent] = useState<MarkingOpenIntent | null>(null);
  const [checkRunId, setCheckRunId] = useState<string | null>(null);

  const openMarkingDocument = useCallback((documentId: string) => {
    setMarkingIntent({ type: "document", documentId });
    setTab("marking");
  }, []);

  const openMarkingFromCheck = useCallback((checkRunId: string, filename: string) => {
    setMarkingIntent({ type: "checkRun", checkRunId, filename });
    setTab("marking");
  }, []);

  const openCheckRun = useCallback((runId: string) => {
    setCheckRunId(runId);
    setTab("check");
  }, []);

  const clearMarkingIntent = useCallback(() => {
    setMarkingIntent(null);
  }, []);

  const clearCheckRunIntent = useCallback(() => {
    setCheckRunId(null);
  }, []);

  return (
    <Layout activeTab={tab} onTabChange={setTab}>
      <div className={tab === "check" ? styles.panel : styles.panelHidden}>
        <EskdAgentPage openCheckRunId={checkRunId} onOpenCheckHandled={clearCheckRunIntent} />
      </div>
      <div className={tab === "history" ? styles.panel : styles.panelHidden}>
        <HistoryPage />
      </div>
      <div className={tab === "marking" ? styles.panel : styles.panelHidden}>
        <MarkingPage openIntent={markingIntent} onOpenIntentHandled={clearMarkingIntent} />
      </div>
      <div className={tab === "knowledge" ? styles.panel : styles.panelHidden}>
        <KnowledgeBasePage onOpenMarking={openMarkingDocument} onOpenMarkingFromCheck={openMarkingFromCheck} />
      </div>
      <div className={tab === "stats" ? styles.panel : styles.panelHidden}>
        <StatsPage />
      </div>
      <div className={tab === "integration" ? styles.panel : styles.panelHidden}>
        <IntegrationLogPage />
      </div>
    </Layout>
  );
}
