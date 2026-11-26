import { MotionConfig } from 'framer-motion';
import { Navbar } from './components/Navbar';
import { HeroSection } from './components/HeroSection';
import { ArchitectureExplorer } from './components/ArchitectureExplorer';
import { BenchmarkExplorer } from './components/BenchmarkExplorer';
import { FailureSimulator } from './components/FailureSimulator';
import { RecoveryTraceExplorer } from './components/RecoveryTraceExplorer';
import { WellArchitectedSection } from './components/WellArchitectedSection';
import { EvidenceDownloadsSection } from './components/EvidenceDownloadsSection';
import { Footer } from './components/Footer';

export function App() {
  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen bg-[#0B0D11] text-[#F3F4F6] antialiased">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-lg focus:bg-[#E7A34D] focus:px-3.5 py-2 focus:text-xs focus:font-semibold focus:text-[#0B0D11]"
        >
          Skip to main content
        </a>

        <Navbar />

        <main id="main-content" tabIndex={-1} className="focus:outline-none">
          <HeroSection />
          <ArchitectureExplorer />
          <BenchmarkExplorer />
          <FailureSimulator />
          <RecoveryTraceExplorer />
          <WellArchitectedSection />
          <EvidenceDownloadsSection />
        </main>

        <Footer />
      </div>
    </MotionConfig>
  );
}

export default App;
