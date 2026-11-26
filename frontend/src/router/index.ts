import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import ReviewWorkbenchView from '@/views/ReviewWorkbenchView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/review_workbench', name: 'review-workbench', component: ReviewWorkbenchView },
    { path: '/review-workbench', component: ReviewWorkbenchView },
    { path: '/confidence_review/workbench', component: ReviewWorkbenchView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
