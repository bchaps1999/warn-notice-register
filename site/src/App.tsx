import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/layout/Shell";
import { Dashboard } from "./pages/Dashboard";
import { Explorer } from "./pages/Explorer";
import { StateProfile } from "./pages/StateProfile";
import { StatesIndex } from "./pages/StatesIndex";
import { NoticeDetailPage } from "./pages/NoticeDetail";
import { NotFound } from "./pages/NotFound";

export function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Dashboard />} />
          <Route path="/explore" element={<Explorer />} />
          <Route path="/states" element={<StatesIndex />} />
          <Route path="/states/:xx" element={<StateProfile />} />
          <Route path="/notice/:key" element={<NoticeDetailPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
