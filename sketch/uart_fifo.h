#pragma once
#include <stm32u5xx.h>
#include <zephyr/irq.h>

// Uno Q core 1.0.0 leaves the STM32 UART FIFOs disabled. At 2 Mbps a byte
// arrives every 5 us; enable the hardware FIFO before starting RPC traffic.
static void enableUartFifo(USART_TypeDef* port) {
  const unsigned key = irq_lock();
  const uint32_t cr1 = port->CR1;
  port->CR1 = cr1 & ~USART_CR1_UE;
  port->CR1 = (cr1 & ~USART_CR1_UE) | USART_CR1_FIFOEN;
  port->ICR = USART_ICR_ORECF | USART_ICR_FECF | USART_ICR_NECF | USART_ICR_PECF;
  port->CR1 = cr1 | USART_CR1_FIFOEN;
  irq_unlock(key);
}
