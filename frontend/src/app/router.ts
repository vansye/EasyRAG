import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'documents',
      component: () => import('@/features/documents/DocumentListView.vue'),
    },
    {
      path: '/ask',
      name: 'ask',
      component: () => import('@/features/qa/AskView.vue'),
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router
