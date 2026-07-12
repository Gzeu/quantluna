/**
 * pages/index.tsx — S37 improved
 * Fix: NavBar default import (nu named), eliminat activePage prop
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import NavBar           from '../components/NavBar';
import { MetricsBadge }   from '../components/MetricsBadge';
import { PnlChart }       from '../components/PnlChart';
import { StrategyScores } from '../components/StrategyScores';
import { WatchdogPanel }  from '../components/WatchdogPanel';

const Home: NextPage = () => (
  <>
    <Head>
      <title>QuantLuna Dashboard</title>
      <meta name="description" content="QuantLuna — Crypto Pairs Trading Live Dashboard" />
    </Head>

    <NavBar />

    <main className="min-h-screen bg-gray-950 text-white px-4 pt-16 pb-10 md:px-8">
      <section className="mb-5"><MetricsBadge /></section>
      <section className="mb-5"><PnlChart maxPoints={300} /></section>
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <StrategyScores />
        <WatchdogPanel />
      </section>
    </main>
  </>
);

export default Home;
