import { useCallback, useState } from "react";
import Layout, { type AppTab } from "@/components/Layout";
import EskdAgentPage from "@/pages/EskdAgentPage";
import HistoryPage from "@/pages/HistoryPage";
import KnowledgeBasePage from "@/pages/KnowledgeBasePage";
import IntegrationLogPage from "@/pages/IntegrationLogPage";
import MarkingPage from "@/pages/MarkingPage";
import StatsPage from "@/pages/StatsPage";
import styles from "./App.module.css";

export default function App() {
  const [tab, setTab] = useState<AppTab>("check");
  const [markingDocumentId, setMarkingDocumentId] = useState<string | null>(null);

  const openMarkingDocument = useCallback((documentId: string) => {
    setMarkingDocumentId(documentId);
    setTab("marking");
  }, []);

  const clearMarkingDocumentIntent = useCallback(() => {
    setMarkingDocumentId(null);
  }, []);

  return (
    <Layout activeTab={tab} onTabChange={setTab}>
      <div className={tab === "check" ? styles.panel : styles.panelHidden}>
        <EskdAgentPage />
      </div>
      <div className={tab === "history" ? styles.panel : styles.panelHidden}>
        <HistoryPage />
      </div>
      <div className={tab === "marking" ? styles.panel : styles.panelHidden}>
        <MarkingPage
          openDocumentId={markingDocumentId}
          onOpenDocumentHandled={clearMarkingDocumentIntent}
        />
      </div>
      <div className={tab === "knowledge" ? styles.panel : styles.panelHidden}>
        <KnowledgeBasePage onOpenMarking={openMarkingDocument} />
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
