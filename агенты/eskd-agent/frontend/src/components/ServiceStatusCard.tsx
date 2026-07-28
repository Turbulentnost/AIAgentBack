import ModelStatusIndicator from "@/components/ModelStatusIndicator";

export default function ServiceStatusCard() {
  return (
    <div className="service-status-box card">
      <h3 className="service-status-title">Статус сервисов</h3>
      <ModelStatusIndicator stacked />
    </div>
  );
}
