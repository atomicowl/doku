package com.example.orders;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.kafka.annotation.KafkaHandler;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
@KafkaListener(topics = "payment-events", groupId = "payments-svc")
public class PaymentEventsConsumer {

    @Autowired
    private OrderRepository orderRepository;

    @KafkaHandler
    public void onPaymentCaptured(PaymentCaptured event) {
        orderRepository.markPaid(event.getOrderId());
    }

    @KafkaHandler(isDefault = true)
    public void onUnknownEvent(Object event) {
        // ignore
    }
}
